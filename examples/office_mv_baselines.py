"""M&V on real office buildings (Models 6, 4, 7) using the open Building Data
Genome Project 2 dataset: 2016 is the baseline year, 2017 the reporting year.

    python examples/office_mv_baselines.py | tee out/mv_output.txt

For every office with full 2016 and 2017 data that passes the load-data checks:
  1. fit a change-point baseline on 2016 daily energy vs temperature (Model 6)
     and check it against the BPA / ASHRAE Guideline 14 validity thresholds
  2. weather-normalize both years to the same "normal" weather (Model 4)
  3. track 2017 against the 2016 baseline with the CUSUM drift alarm (Model 7)

Uses the cached BDG2 files from examples/office_load_analytics.py (downloads the
weather file, ~10 MB, on first run). Data: Miller et al. (2020), CC BY 4.0.
"""

from collections import Counter
from pathlib import Path

import pandas as pd

from ewmodels import load, mv
from ewmodels.data import bdg2

pd.set_option("display.width", 200)
OUT = Path("out")
OUT.mkdir(exist_ok=True)

meta = bdg2.metadata().set_index("building_id")
cols = set(bdg2.electricity_columns())
offices = [b for b in meta.index[meta["primaryspaceusage"] == "Office"]
           if b in cols and isinstance(meta.loc[b, "timezone"], str)]
energy = bdg2.load_buildings(offices)
weather = bdg2.load_weather(sorted({meta.loc[b, "site_id"] for b in offices}))

rows, skipped = [], Counter()
for b, s in energy.items():
    site = meta.loc[b, "site_id"]
    tz = str(s.index.tz)
    t = weather.get(site)
    e16, e17 = s[s.index.year == 2016], s[s.index.year == 2017]
    if t is None or len(t) < 8000:
        skipped["no usable weather"] += 1
        continue
    if len(e16) < 0.95 * 8784 or len(e17) < 0.95 * 8760:
        skipped["under 95% of 2016 or 2017 metered"] += 1
        continue
    if not (load.load_profile(e16, tz=tz).receipt.valid and load.load_profile(e17, tz=tz).receipt.valid):
        skipped["failed load-data checks (spikes, outages, gaps)"] += 1
        continue
    t16, t17 = t[t.index.year == 2016], t[t.index.year == 2017]
    try:
        f16 = mv.fit_baseline(e16, t16, tz=tz)
        f17 = mv.fit_baseline(e17, t17, tz=tz)
    except ValueError as exc:
        skipped[f"baseline could not be fitted: {exc}"] += 1
        continue
    base = f16.value
    failed = [c.name for c in f16.receipt.checks if not c.passed]
    normal = mv.normal_year_temps(t, tz=tz, year=2017)
    n16 = mv.normalized_consumption(base, normal).value
    n17 = mv.normalized_consumption(f17.value, normal).value
    cu = mv.cusum(base, e17, t17, tz=tz).value
    rows.append({
        "building": b, "site": site, "model": base.model,
        "cvrmse": base.stats["cvrmse"], "r2": base.stats["r2"],
        "passes_standard_stats": not [c for c in failed if c != "self_check"],
        "valid_baseline": not failed, "failed_checks": ",".join(failed),
        "weather_normalized_change": (n17 - n16) / n16,
        "actual_change": float(e17.sum() / e16.sum() - 1),
        "deviation_vs_baseline_2017": cu["deviation_pct"],
        "drift_up": cu["first_alarm_up"] is not None,
        "drift_down": cu["first_alarm_down"] is not None,
        "first_alarm": min([d for d in (cu["first_alarm_up"], cu["first_alarm_down"]) if d is not None], default=None),
        "min_detectable": cu["min_detectable_pct"],
    })

t = pd.DataFrame(rows)
t.to_csv(OUT / "office_mv_summary.csv", index=False)
print(f"Offices analysed: {len(t)} across {t['site'].nunique()} sites (baseline 2016, reporting 2017)")
print("Skipped:")
for why, n in skipped.most_common():
    print(f"  {n:4d}  {why}")

std = t[t["passes_standard_stats"]]
v = t[t["valid_baseline"]]
print(f"\n=== Model 6: baselines ===")
print(f"Pass the standard statistics (CV(RMSE)<20%, |bias|<0.5%, R2>0.5, |t|>1.3): "
      f"{len(std)} of {len(t)} ({len(std) / len(t):.0%})")
print(f"...and also pass the self-check (no drift against their own baseline year): "
      f"{len(v)} of {len(t)} ({len(v) / len(t):.0%})")
print(f"So {len(std) - len(v)} of {len(std)} baselines ({(len(std) - len(v)) / len(std):.0%}) that look fine "
      f"on the standard statistics hide a recurring pattern the model misses.")
r2_only = t[t["failed_checks"].fillna("").map(lambda f: "r2" in f.split(",") and set(f.split(",")) <= {"r2", "self_check"})]
print(f"Failed only on R2 (steady but weather-insensitive): {len(r2_only)}, "
      f"median CV(RMSE) {r2_only['cvrmse'].median():.1%}")
print("All failed checks:")
for why, n in Counter(",".join(t.loc[~t["valid_baseline"], "failed_checks"]).split(",")).most_common():
    if why:
        print(f"  {n:4d}  {why}")
print("\nBest-fit model form among valid baselines:")
for m, n in v["model"].value_counts().items():
    print(f"  {n:4d}  ({n / len(v):.0%})  {m}")
print(f"Median CV(RMSE), valid baselines: {v['cvrmse'].median():.1%}")

print("\n=== Model 4: weather-normalized change, 2016 -> 2017 (valid baselines) ===")
wn, act = v["weather_normalized_change"], v["actual_change"]
print(f"Median actual change:            {act.median():+.1%}")
print(f"Median weather-normalized change: {wn.median():+.1%}")
print(f"Offices where weather flipped the sign of the change: {((wn > 0) != (act > 0)).sum()} of {len(v)}")

print("\n=== Model 7: 2017 tracked against the 2016 baseline (valid baselines) ===")
up, dn = v["drift_up"], v["drift_down"]
print(f"Alarms: up only {(up & ~dn).sum()}, down only {(dn & ~up).sum()}, both {(up & dn).sum()}, "
      f"none {(~up & ~dn).sum()} (of {len(v)})")
print(f"Median minimum detectable change: {v['min_detectable'].median():.1%}")
dev = v["deviation_vs_baseline_2017"]
print(f"Median 2017 deviation from baseline: {dev.median():+.1%} (IQR {dev.quantile(.25):+.1%} to {dev.quantile(.75):+.1%})")
fa = pd.to_datetime(v["first_alarm"], utc=True).dt.month
print("Month of first 2017 alarm:", fa.value_counts().sort_index().astype(int).to_dict())
print("Note: campus calendars shift between years (term dates, holidays). Without day_flags,")
print("some 2017 alarms reflect calendar differences rather than operational change.")

print("\n=== By site ===")
print(t.assign(drift_any=t["drift_up"] | t["drift_down"]).groupby("site").agg(
    offices=("building", "count"), pass_standard=("passes_standard_stats", "mean"),
    pass_self_check=("valid_baseline", "mean"), median_cvrmse=("cvrmse", "median")).round(3).to_string())
