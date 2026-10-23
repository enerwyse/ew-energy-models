# ew-energy-models

[![PyPI](https://img.shields.io/pypi/v/ew-energy-models)](https://pypi.org/project/ew-energy-models/)
[![Python](https://img.shields.io/pypi/pyversions/ew-energy-models)](https://pypi.org/project/ew-energy-models/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![tests](https://github.com/enerwyse/ew-energy-models/actions/workflows/tests.yml/badge.svg)](https://github.com/enerwyse/ew-energy-models/actions/workflows/tests.yml)

**Open, auditable energy-analytics models from [Enerwyse](https://enerwyse.ca).**

`ewmodels` is a Python library of energy models for commercial and industrial
facilities: facility load analytics, Ontario Global Adjustment / Industrial
Conservation Initiative (ICI) peak tools, and, next, measurement and
verification (M&V) baselines.

Every model returns a result **and an evidence receipt**. The receipt records:

- the input data hash
- the detected interval resolution
- data checks, each pass/fail (gaps, spikes, meter outages, duplicates, ...)
- the simplifying assumptions the result relies on
- the code version that produced it

A number without its receipt is not a result.

```bash
pip install ew-energy-models
```

```python
from ewmodels import load

profile = load.load_profile(meter_kwh)   # kWh per interval, timezone-aware index, any resolution
print(profile.value["summary"])          # baseload, typical and high load, always-on share, ...
print(profile.receipt.summary())         # what this number rests on, and whether it's valid
```

## Why this exists

Energy analytics usually comes as a black box: a vendor's number on a
dashboard. Facility managers, M&V reviewers and incentive programs then have to
take it on trust. We think the math should be open and every figure should be
checkable. The value a platform adds is applying the math reliably to real
operations, not hiding it.

## Models

The library implements the Enerwyse Models Catalogue one model family at a time.
Numbers refer to the catalogue.

| # | Model | Status | Since | Where |
|---|---|---|---|---|
| 1 | Load profile decomposition (baseload, profiles, always-on share) | ✅ Released | v0.3.0 | `ewmodels.load.load_profile` |
| 2 | Peak demand ranking and system-peak coincidence | ✅ Released | v0.3.0 | `ewmodels.load.rank_peaks` |
| 3 | Energy cost attribution (market price + GA + delivery) | Being re-scoped | | |
| 4 | Weather-normalized consumption | Planned | v0.4 | `ewmodels.mv` |
| 5 | Load factor | ✅ Released | v0.3.0 | `ewmodels.load.load_factor` |
| 6 | ASHRAE Guideline 14 change-point baselines (3P/4P/5P) | Planned | v0.4 | `ewmodels.mv` |
| 7 | CUSUM drift tracking | Planned | v0.4 | `ewmodels.mv` |
| 8 | Short-term load forecasting | Planned | | |
| 9 | Global Adjustment coincident peaks: Peak Atlas, open 5CP benchmark, forecast archive | ✅ Released | v0.2.0 | `ewmodels.ontario.atlas`, `.benchmark`, `ewmodels.data.ieso_forecast` |
| 10 | Peak Demand Factor and curtailment simulation | ✅ Released | v0.2.0 | `ewmodels.ontario.ici` |
| 11 | Load-shifting optimizer | Planned | | |
| 12 | Energy cost forecasting | Planned | | |
| 13 | Anomaly detection | Planned | | |
| 14 | Energy performance benchmarking | Planned | | |
| 15 | Non-intrusive load monitoring (NILM) | Research | | |
| 16 | Grid carbon-intensity optimization | Research | | |
| 17 | Battery / storage dispatch optimization | Research | | |

Model 9 deliberately does not include a peak *predictor*. Our analysis found
that the length of the curtailment window matters more than picking the day
(see below), so the toolkit focuses on measuring that trade-off openly.

## Facility load analytics (v0.3)

Three models for any interval meter (5-, 15- or 60-minute; resolution is detected):

| Function | Model | What it tells you |
|---|---|---|
| `load.load_profile()` | 1 | Baseload (P10), typical (P50) and high load (P90); the weekday and weekend daily shape; **always-on share**, the fraction of energy used at the building's baseline load |
| `load.rank_peaks()` | 2 | The top-N demand peaks and when they cluster. Given system peak hours, such as Ontario's ICI hours, it also returns the **coincidence factor**: how close the site runs to its own maximum when the grid peaks |
| `load.load_factor()` | 5 | Monthly load factor with coverage checks |

Every load receipt checks for duplicate timestamps, negative readings, coverage,
regular spacing, record length, **spikes** (max above 2× the 99th percentile)
and **meter-outage runs** (more than 5% of readings at or near zero).

```python
from ewmodels import load

profile = load.load_profile(meter_kwh)
peaks = load.rank_peaks(meter_kwh, system_peak_hours=ici_hours)
print(profile.value["summary"]["always_on_share"], peaks.value["coincidence"]["coincidence_factor"])
```

`examples/office_load_analytics.py` runs all three on every office in the open
[Building Data Genome Project 2](https://github.com/buds-lab/building-data-genome-project-2)
(Miller et al., 2020, CC BY 4.0) with a full year of data, downloaded at
runtime. Of 296 offices, 216 across 11 sites passed every check. The median
office used **64%** of its energy at baseline load, and every site's median
office was above half.

## Ontario ICI toolkit (v0.2)

| Module | What it does |
|---|---|
| `ewmodels.data.ieso` | Downloads and parses IESO hourly Ontario demand and the **ICI Demand** reports. Converts hour-ending EST to interval-start timestamps. Caches locally. |
| `ewmodels.data.ieso_forecast`, `ewmodels.data.ieso_backfill` | Parses IESO's Ontario demand forecasts (Adequacy3) and maintains the **open forecast archive** |
| `ewmodels.ontario.ici` | Base periods, top-5 peak hours, **Peak Demand Factor** from interval meter data, GA charge, an optional **clean-energy PPA offset**, and curtailment-savings simulation |
| `ewmodels.ontario.atlas` | **Ontario Peak Atlas**: per-season thresholds, peak timing, and *crowding*, i.e. how many days came within 1–5% of the 5th peak |
| `ewmodels.ontario.benchmark` | **Open 5CP benchmark**: replays history day by day with no look-ahead and scores any peak-calling strategy on *peaks captured vs. days curtailed*. Supports synthetic or real forecasts, hour-level scoring with a curtailment window, window-length analysis, and a **growth-removed stress test** |

Reproduced peak hours match IESO's own ICI demand reports **25 of 25** for
2022–2026. In 4 of 5 seasons all five peak hours fell on a single afternoon,
and catching every peak hour in every season needed a 3–9 pm curtailment
window. The best 4-hour window caught 85%.

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
forecaster and compare it on the same terms. Two honesty guards are built in:

- **Stress test.** Ontario's peak has grown every recent season, which flatters any
  rule anchored on last year's peaks. `normalize_seasons()` removes that growth, so
  a strategy has to work in a flat year too.
- **Real windows.** `window=(start, hours)` scores what a fixed curtailment window
  actually catches, and `window_coverage()` shows how long the window has to be.

### The open IESO forecast archive

IESO keeps every version of its Ontario demand forecasts for only about 45
days, so no public record exists of what was forecast against what happened.
This repo builds one: a scheduled workflow archives IESO's day-ahead forecasts
twice a day into `data/ieso_forecast_archive.csv`, filling any missed days that
are still within IESO's window. Once a full ICI season is archived (summer
2027), the benchmark can score strategies on the forecasts operators actually saw.

## Versions

`ew-energy-models` follows [semantic versioning](https://semver.org). While the
version starts with 0:

- **Minor releases (0.x.0)** add a model family, and may change existing
  function signatures. Any such change is listed in the release notes.
- **Patch releases (0.x.y)** fix bugs and tighten data checks without changing
  results for valid input.

Version 1.0.0 will mark a stable API.

| Version | Date | What's in it |
|---|---|---|
| [0.3.0](https://github.com/enerwyse/ew-energy-models/releases/tag/v0.3.0) | 2026-10-23 | Facility load analytics (Models 1, 2, 5); spike and meter-outage checks; Building Data Genome 2 loader |
| [0.2.0](https://github.com/enerwyse/ew-energy-models/releases/tag/v0.2.0) | 2026-10-22 | Ontario ICI toolkit (Models 9, 10): Peak Atlas, open 5CP benchmark, IESO cross-check, open forecast archive |

Full notes for each release are in [CHANGELOG.md](CHANGELOG.md).

**Next: v0.4 (M&V).** ASHRAE Guideline 14 change-point baselines, weather
normalization and CUSUM tracking (Models 6, 4, 7), with CV(RMSE) and NMBE
validity checks in every receipt.

## Design principles

1. **Receipts, not just results.** Checks fail loudly: for example, a missing meter
   reading during a peak hour makes the PDF receipt invalid, rather than
   silently computing a wrong PDF.
2. **Resolution is data, not configuration.** Utility interval data arrives at
   5, 15 or 60 minutes. Models detect the resolution and never assume it.
3. **Deterministic and reproducible.** The same inputs always give the same
   outputs, and randomness is seeded.
4. **No customer data, ever.** The library ships no meter data. Examples use
   public sources only (IESO, open building datasets).

## Install

```bash
pip install ew-energy-models
# or, to develop:
git clone https://github.com/enerwyse/ew-energy-models && cd ew-energy-models
python3 -m venv ~/.venvs/ew-energy-models && source ~/.venvs/ew-energy-models/bin/activate
pip install -e ".[dev,plot]" && pytest
```

## Important

This is an analysis tool, not a settlement engine. Peak hours reproduced from
public demand data are provisional until the IESO publishes the official ones.
PDFs computed with Ontario Demand as the denominator are estimates. Your LDC or
IESO invoice is authoritative. Nothing here is financial or regulatory advice.

## Maintenance

See [MAINTENANCE.md](MAINTENANCE.md) for the runbook: weekly checks, alerts,
yearly updates and releases.

## License & citation

Apache-2.0. If you use this in research, please cite it (see `CITATION.cff`).
IESO data © Independent Electricity System Operator; Building Data Genome 2 data
by Miller et al. (CC BY 4.0). Both are fetched at runtime, not redistributed.
