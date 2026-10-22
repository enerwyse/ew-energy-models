"""Ontario Peak Atlas + 5CP benchmark on real IESO data.

    python examples/ontario_peak_atlas.py            # writes ./out/*.csv (+ PNG if matplotlib)

Uses base periods from 2022-2023 onward: since May 2022 the ICI peak hours
have been determined from real-time Ontario demand, so earlier seasons aren't
like-for-like.
"""

from datetime import date
from pathlib import Path

from ewmodels.data.ieso import EST, fetch_demand
from ewmodels.ontario import atlas, benchmark

OUT = Path("out")
OUT.mkdir(exist_ok=True)

demand = fetch_demand(range(2022, date.today().year + 1))["ontario_demand_mw"]
demand = demand[demand.index >= "2022-05-01 00:00:00-05:00"]
print(f"Loaded {len(demand):,} hours, {demand.index.min()} .. {demand.index.max()} ({EST})")

res = atlas.peak_atlas(demand)
print("\n=== Peak Atlas: summary per base period ===")
print(res.value["summary"].to_string(index=False))
print("\n=== Top-5 peak hours ===")
print(res.value["peaks"].to_string(index=False))
print("\n=== Peak hours by hour-ending (EST) ===")
print(res.value["by_hour"].to_string())
print("\n" + res.receipt.summary())
for k in ("summary", "peaks"):
    res.value[k].to_csv(OUT / f"atlas_{k}.csv", index=False)
(OUT / "atlas_receipt.json").write_text(res.receipt.to_json(indent=2))

print("\n=== 5CP benchmark: threshold rule, days called vs. peaks captured ===")
curve = benchmark.tradeoff_curve(demand)
print(curve.to_string(index=False))
curve.to_csv(OUT / "benchmark_tradeoff.csv", index=False)

try:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 4.5))
    for err, g in curve.groupby("forecast_error_pct"):
        ax.plot(g["mean_days_called"], 100 * g["mean_capture_rate"], marker="o",
                label="perfect forecast" if err == 0 else f"±{err:g}% forecast error")
    ax.set_xlabel("Curtailment days per season")
    ax.set_ylabel("Peak hours captured (%)")
    ax.set_title("What it costs to catch Ontario's top-5 peaks")
    ax.grid(alpha=0.3)
    ax.legend(frameon=False)
    fig.text(0.01, 0.01, "Data: IESO public reports · ew-energy-models by Enerwyse", fontsize=7, alpha=0.6)
    fig.tight_layout()
    fig.savefig(OUT / "benchmark_tradeoff.png", dpi=200)
    print(f"\nChart: {OUT / 'benchmark_tradeoff.png'}")
except ImportError:
    print("\n(install matplotlib for the chart: pip install 'ew-energy-models[plot]')")
