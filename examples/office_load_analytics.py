"""Load analytics (Models 1, 2, 5) on real office buildings from the open
Building Data Genome Project 2 dataset.

    python examples/office_load_analytics.py | tee out/load_output.txt

Analyses EVERY office with a full year of data (not a convenience sample),
reports results by site so one campus can't dominate, and excludes buildings
whose evidence receipt fails, saying why.

First run downloads the BDG2 electricity file (~150+ MB) and caches it.
Data: Miller et al. (2020), Scientific Data 7, 368, CC BY 4.0.
"""

from collections import Counter
from pathlib import Path

import pandas as pd

from ewmodels import load
from ewmodels.data import bdg2

pd.set_option("display.width", 200)
OUT = Path("out")
OUT.mkdir(exist_ok=True)
YEAR = 2017


def band(h):
    if 5 <= h <= 10:
        return "morning start-up (5–10)"
    if 11 <= h <= 14:
        return "midday (11–14)"
    if 15 <= h <= 20:
        return "afternoon/evening (15–20)"
    return "night (21–4)"


meta = bdg2.metadata().set_index("building_id")
cols = set(bdg2.electricity_columns())
offices = [b for b in meta.index[meta["primaryspaceusage"] == "Office"] if b in cols and isinstance(meta.loc[b, "timezone"], str)]
print(f"BDG2 offices with electricity data: {len(offices)}")

series = bdg2.load_buildings(offices)
rows, excluded, kept = [], Counter(), {}
for b, s in series.items():
    s = s[s.index.year == YEAR]
    if len(s) < 0.95 * 8760:
        excluded["under 95% of the year metered"] += 1
        continue
    tz = str(s.index.tz)
    prof = load.load_profile(s, tz=tz)
    failed = [c.name for c in prof.receipt.checks if not c.passed]
    if failed:
        excluded["receipt failed: " + ", ".join(failed)] += 1
        continue
    peaks = load.rank_peaks(s, n=10, tz=tz).value
    lf = load.load_factor(s, tz=tz).value
    lf = lf[lf["complete"]]
    sm = prof.value["summary"]
    mode_h = peaks["clustering"]["most_common_hour"]
    rows.append({
        "building": b, "site": meta.loc[b, "site_id"], "sqm": meta.loc[b, "sqm"],
        "baseload_kw": sm["baseload_kw"], "max_kw": sm["max_kw"],
        "always_on_share": sm["always_on_share"], "base_to_peak": sm["base_to_peak"],
        "peak_hour_mode": mode_h, "peak_band": band(mode_h),
        "peaks_on_weekdays": peaks["clustering"]["share_on_weekdays"],
        "median_monthly_lf": lf["load_factor"].median(),
    })
    kept[b] = prof

t = pd.DataFrame(rows)
t.to_csv(OUT / "office_load_summary.csv", index=False)
print(f"\nAnalysed: {len(t)} offices across {t['site'].nunique()} sites (calendar {YEAR})")
print("Excluded:")
for why, n in excluded.most_common():
    print(f"  {n:4d}  {why}")

print("\n=== By site (medians) ===")
by_site = t.groupby("site").agg(
    offices=("building", "count"),
    always_on_share=("always_on_share", "median"),
    base_to_peak=("base_to_peak", "median"),
    monthly_load_factor=("median_monthly_lf", "median"),
    morning_peakers=("peak_band", lambda x: (x == "morning start-up (5–10)").mean()),
).sort_values("offices", ascending=False)
print(by_site.round(2).to_string())

site_med = by_site["always_on_share"]
print("\n=== Portfolio findings ===")
print(f"Median always-on share, all offices: {t['always_on_share'].median():.0%} "
      f"(IQR {t['always_on_share'].quantile(.25):.0%}–{t['always_on_share'].quantile(.75):.0%})")
print(f"Median of site medians (each site counts once): {site_med.median():.0%} "
      f"(range across sites {site_med.min():.0%}–{site_med.max():.0%})")
print(f"Offices using more than half their energy as always-on baseload: "
      f"{(t['always_on_share'] > 0.5).sum()} of {len(t)} ({(t['always_on_share'] > 0.5).mean():.0%})")
print(f"Sites where the median office is over half always-on: {(site_med > 0.5).sum()} of {len(site_med)}")
print("\nWhen offices hit their own peaks (most common hour of their top-10 days, local time):")
for k, v in t["peak_band"].value_counts().items():
    print(f"  {v:4d}  ({v / len(t):.0%})  {k}")

try:
    import matplotlib.pyplot as plt

    med = t["always_on_share"].median()
    pick = t.iloc[(t["always_on_share"] - med).abs().argsort().iloc[0]]["building"]
    p = kept[pick].value["profile"]
    sm = kept[pick].value["summary"]
    fig, ax = plt.subplots(figsize=(7.5, 4.5), dpi=200)
    for dt, color in (("weekday", "#2a78d6"), ("weekend", "#8a8a85")):
        ax.fill_between(p.index, p[(dt, "p10")], p[(dt, "p90")], color=color, alpha=0.18, linewidth=0)
        ax.plot(p.index, p[(dt, "p50")], color=color, linewidth=2, label=f"{dt}: median, with P10–P90 band")
    ax.axhline(sm["baseload_kw"], color="#52514e", linewidth=1, linestyle="--")
    ax.text(23, sm["baseload_kw"], f"baseload {sm['baseload_kw']:.0f} kW ", va="bottom", ha="right", fontsize=8, color="#52514e")
    ax.set_xlim(0, 23)
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Hour of day (local)")
    ax.set_ylabel("Demand (kW)")
    ax.set_title(f"A typical office: {sm['always_on_share']:.0%} of its energy is always-on", loc="left")
    ax.grid(alpha=0.3)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    fig.text(0.01, 0.01, f"Data: Building Data Genome 2 ({pick}, {YEAR}), CC BY 4.0 · ew-energy-models by Enerwyse",
             fontsize=6.5, alpha=0.7)
    fig.tight_layout()
    fig.savefig(OUT / "office_daily_profile.png")
    print(f"\nChart: {OUT / 'office_daily_profile.png'} (median office: {pick})")
except ImportError:
    print("\n(install matplotlib for the chart: pip install 'ew-energy-models[plot]')")
