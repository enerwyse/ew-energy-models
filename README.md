# ew-energy-models

**Open, auditable energy-analytics models from [Enerwyse](https://enerwyse.ca).**

`ewmodels` is a small Python library of energy models for commercial and
industrial facilities. It covers load analytics, M&V baselines, and Ontario
Global Adjustment / Industrial Conservation Initiative (ICI) peak tools.

Every model returns a result **and an evidence receipt**. The receipt records:

- the input data hash
- the detected interval resolution
- data-sufficiency checks, each pass/fail
- the simplifying assumptions the result relies on
- the code version that produced it

A number without its receipt is not a result.

```python
from ewmodels.data.ieso import fetch_demand
from ewmodels.ontario import atlas, ici

demand = fetch_demand(range(2022, 2027))["ontario_demand_mw"]
res = atlas.peak_atlas(demand)

print(res.value["summary"])        # per base period: 5th-peak threshold, crowding
print(res.receipt.summary())       # what this number rests on
```

## Why this exists

Energy analytics usually comes as a black box: a vendor's number on a
dashboard. Facility managers, M&V reviewers and incentive programs then have to
take it on trust. We think the math should be open and every figure should be
checkable. The value a platform adds is applying the math reliably to real
operations, not hiding it.

## What's in v0.2: Ontario ICI toolkit

| Module | What it does |
|---|---|
| `ewmodels.data.ieso` | Downloads and parses IESO hourly Ontario demand and the **ICI Demand** reports. Converts hour-ending EST to interval-start timestamps. Caches locally. |
| `ewmodels.data.ieso_forecast` | Parses IESO's Ontario demand forecasts (Adequacy3) and keeps an **open forecast archive** |
| `ewmodels.ontario.ici` | Base periods, top-5 peak hours, **Peak Demand Factor** from interval meter data, GA charge, an optional **clean-energy PPA offset**, and curtailment-savings simulation |
| `ewmodels.ontario.atlas` | **Ontario Peak Atlas**: per-season thresholds, peak timing, and *crowding*, i.e. how many days came within 1–5% of the 5th peak |
| `ewmodels.ontario.benchmark` | **Open 5CP benchmark**: replays history day by day with no look-ahead and scores any peak-calling strategy on *peaks captured vs. days curtailed*. Supports synthetic or real forecasts, hour-level scoring with a curtailment window, window-length analysis, and a **growth-removed stress test** |

### The open 5CP benchmark

Accuracy scores don't capture what a Class A customer actually trades off. A
strategy that calls every hot weekday catches every peak and disrupts
operations all summer. One that calls three days may miss the peak that
mattered. The benchmark gives a strategy only what was knowable before each
day, then scores it in operational terms:

```python
from ewmodels.ontario import benchmark

result = benchmark.evaluate(demand, benchmark.threshold_strategy(margin=0.03),
                            forecast_error_pct=2.0)
print(result.value)   # per base period: days_called, peak_hours_captured, capture_rate
```

Any function `DayContext -> bool` is a strategy, so you can plug in your own
forecaster and compare it on the same terms.

**Two honesty guards are built in:**

- **Stress test.** Ontario's peak has grown every recent season, which flatters any
  rule anchored on last year's peaks. `normalize_seasons()` removes that growth, so
  a strategy has to work in a flat year too.
- **Real windows.** All five peak hours usually fall on one afternoon, as
  consecutive hours. `window=(start, hours)` scores what a fixed curtailment window
  actually catches, and `window_coverage()` shows how long the window has to be.

### The open IESO forecast archive

IESO keeps its Ontario demand forecasts for only about 90 days, so no public
record exists of what was forecast against what happened. This repo builds one:
a scheduled workflow snapshots IESO's forecasts twice a day into
`data/ieso_forecast_archive.csv`. Once a full ICI season is archived, the
benchmark can score strategies on the forecasts operators actually saw.

## Design principles

1. **Receipts, not just results.** Checks fail loudly: for example, a missing meter
   reading during a peak hour makes the PDF receipt invalid, rather than
   silently computing a wrong PDF.
2. **Resolution is data, not configuration.** Utility interval data arrives at
   5, 15 or 60 minutes. Models detect the resolution and never assume it.
3. **Deterministic and reproducible.** The same inputs always give the same
   outputs, and randomness is seeded.
4. **No customer data, ever.** The library ships no meter data. Examples use
   public sources only (IESO, Environment Canada, open building datasets).

## Roadmap

The library will grow to cover the Enerwyse models catalogue:

- **v0.3 Load kernels:** load-profile decomposition, peak ranking, load factor
- **v0.4 M&V:** ASHRAE Guideline 14 change-point baselines (3P/4P/5P), weather
  normalization, CUSUM tracking, with CV(RMSE)/NMBE validity in the receipt
- **Later:** day-ahead load forecasting, load-shifting optimization, and
  carbon-intensity scheduling

## Install

```bash
pip install ew-energy-models            # once published
# or from source
pip install -e ".[dev]" && pytest
```

## Important

This is an analysis tool, not a settlement engine. Peak hours reproduced from
public demand data are provisional until the IESO publishes the official ones.
PDFs computed with Ontario Demand as the denominator are estimates. Your LDC or
IESO invoice is authoritative. Nothing here is financial or regulatory advice.

## Maintenance

See [MAINTENANCE.md](MAINTENANCE.md) for the runbook: weekly checks, alerts, yearly updates, releases.

## License & citation

Apache-2.0. If you use this in research, please cite it (see `CITATION.cff`).
IESO data © Independent Electricity System Operator. It is fetched at runtime,
not redistributed.
