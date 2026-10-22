"""Ontario Peak Atlas + 5CP benchmark on real IESO data.

    python examples/ontario_peak_atlas.py | tee out/run_output.txt

Uses base periods from 2022-2023 onward: since May 2022 the ICI peak hours
have been determined from real-time Ontario demand, so earlier seasons aren't
like-for-like.
"""

from datetime import date
from pathlib import Path

import pandas as pd

from ewmodels.data.ieso import fetch_demand, fetch_ici_demand
from ewmodels.ontario import atlas, benchmark, ici

pd.set_option("display.width", 200)
OUT = Path("out")
OUT.mkdir(exist_ok=True)
FIRST = 2022
today = date.today()
current_bp = today.year if today.month >= 5 else today.year - 1


def section(title):
    print(f"\n=== {title} ===")


demand = fetch_demand(range(FIRST, today.year + 1))["ontario_demand_mw"]
demand = demand[demand.index >= f"{FIRST}-05-01 00:00:00-05:00"]
print(f"Loaded {len(demand):,} hours, {demand.index.min()} .. {demand.index.max()}")

# 1. Cross-check against IESO's own ICI demand reports ------------------------
section("1. Cross-check: our peaks vs IESO ICI Demand reports")
ours = ici.top_peak_hours(demand).value
try:
    ici_dem = fetch_ici_demand(range(FIRST, current_bp + 1))["ontario_demand_mw"]
    theirs = ici.top_peak_hours(ici_dem).value
    m = ours.merge(theirs, on=["base_period", "rank"], suffixes=("_ours", "_ieso"))
    m["same_hour"] = m["interval_start_ours"] == m["interval_start_ieso"]
    m["mw_diff"] = m["demand_mw_ours"] - m["demand_mw_ieso"]
    print(m[["base_period", "rank", "interval_start_ours", "interval_start_ieso", "same_hour", "mw_diff"]]
          .to_string(index=False))
    print(f"\nMatching peak hours: {int(m.same_hour.sum())}/{len(m)}")
    m.to_csv(OUT / "crosscheck_ici.csv", index=False)
except Exception as e:  # keep the rest of the run useful
    print(f"ICI cross-check unavailable: {e!r}")

# 2. Atlas ---------------------------------------------------------------------
res = atlas.peak_atlas(demand)
section("2. Peak Atlas: summary per base period")
print(res.value["summary"].to_string(index=False))
section("Top-5 peak hours")
print(res.value["peaks"].to_string(index=False))
print("\n" + res.receipt.summary())
for k in ("summary", "peaks"):
    res.value[k].to_csv(OUT / f"atlas_{k}.csv", index=False)
(OUT / "atlas_receipt.json").write_text(res.receipt.to_json(indent=2))

# 3. Curtailment window length -------------------------------------------------
section("3. Window coverage: perfect day choice, fixed daily window (complete seasons)")
wc = benchmark.window_coverage(demand)
print(wc.to_string(index=False))
wc.to_csv(OUT / "window_coverage.csv", index=False)

# 4. Benchmark: raw vs growth-removed stress test ------------------------------
norm = benchmark.normalize_seasons(demand, method="mean")
section("4a. Stress-test scale factors (growth removed, seasons equalized on mean demand)")
print(norm.receipt.parameters["scale_factors"])

best4 = int(wc.set_index("hours").loc[4, "best_start_est"])  # best-placed 4-hour window
curves = {}
for name, series in [("raw", demand), ("stressed", norm.value)]:
    for wname, window in [("whole_day", None), ("4h_window", (best4, 4))]:
        section(f"4b. Benchmark: {name} demand, {wname}{'' if window is None else f' {window} EST'}")
        c = benchmark.tradeoff_curve(series, window=window)
        print(c.to_string(index=False))
        c.to_csv(OUT / f"benchmark_{name}_{wname}.csv", index=False)
        curves[(name, wname)] = c

# 5. Chart ---------------------------------------------------------------------
try:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=True)
    for ax, key, title in [(axes[0], ("raw", "whole_day"), "Actual demand"),
                           (axes[1], ("stressed", "whole_day"), "Growth removed (stress test)")]:
        for err, g in curves[key].groupby("forecast_error_pct"):
            ax.plot(g["mean_days_called"], 100 * g["mean_capture_rate"], marker="o",
                    label="perfect forecast" if err == 0 else f"±{err:g}% forecast error")
        ax.set_title(title)
        ax.set_xlabel("Curtailment days per season")
        ax.set_ylim(0, 102)
        ax.set_xlim(left=0)
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("Peak hours captured (%)")
    axes[1].legend(frameon=False, loc="lower right")
    fig.suptitle("Trailing-threshold rule: what it costs to catch Ontario's top-5 peaks")
    fig.text(0.01, 0.01, "Data: IESO public reports · ew-energy-models by Enerwyse", fontsize=7, alpha=0.6)
    fig.tight_layout()
    fig.savefig(OUT / "benchmark_tradeoff.png", dpi=200)
    print(f"\nChart: {OUT / 'benchmark_tradeoff.png'}")
except ImportError:
    print("\n(install matplotlib for the chart: pip install 'ew-energy-models[plot]')")
