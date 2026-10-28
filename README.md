# ew-energy-models

[![PyPI](https://img.shields.io/pypi/v/ew-energy-models)](https://pypi.org/project/ew-energy-models/)
[![Python](https://img.shields.io/pypi/pyversions/ew-energy-models)](https://pypi.org/project/ew-energy-models/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![tests](https://github.com/enerwyse/ew-energy-models/actions/workflows/tests.yml/badge.svg)](https://github.com/enerwyse/ew-energy-models/actions/workflows/tests.yml)

**Open, auditable energy-analytics models from [Enerwyse](https://enerwyse.ca).**

`ewmodels` is a Python library of energy models for commercial and industrial
facilities: facility load analytics, measurement and verification (M&V)
baselines, Ontario Global Adjustment / Industrial Conservation Initiative
(ICI) peak tools, Class A cost attribution, and open archives of IESO data that
IESO itself only keeps for weeks.

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
| 3 | Class A cost attribution: energy at OEMP + Global Adjustment, Class A vs Class B | ✅ Released | v0.5.0 | `ewmodels.ontario.cost` |
| 4 | Weather-normalized consumption | ✅ Released | v0.4.0 | `ewmodels.mv.normalized_consumption` |
| 5 | Load factor | ✅ Released | v0.3.0 | `ewmodels.load.load_factor` |
| 6 | Change-point baselines (2P/3P/4P/5P) with validity checks and self-check | ✅ Released | v0.4.0 | `ewmodels.mv.fit_baseline` |
| 7 | CUSUM drift tracking with minimum detectable change | ✅ Released | v0.4.0 | `ewmodels.mv.cusum` |
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

## Ontario Class A cost attribution (v0.5)

Class A customers don't pay time-of-use rates. Their commodity bill is energy at
the hourly **Ontario Electricity Market Price** (OEMP = day-ahead Ontario zonal
price + LFDA, since Market Renewal in May 2025) plus **Global Adjustment**, which
is set for a whole July–June year by the site's load in five peak hours of the
previous May–April.

| Function | What it does |
|---|---|
| `ontario.cost.peak_hour_value()` | GA dollars that one MWh used in a base period's 5 peak hours cost over the following adjustment period |
| `ontario.cost.site_pdf()` | A site's Peak Demand Factor for each base period, from its meter data |
| `ontario.cost.bill_breakdown()` | Monthly commodity bill as Class A and as Class B: energy, GA (credits included) and optional delivery rates |
| `data.ieso_prices` | Parsers, loaders and archivers for IESO day-ahead prices, LFDA and monthly GA; `oemp()` combines them |

```python
from ewmodels.data.ieso_prices import load_da_prices, load_ga, fetch_lfda, oemp
from ewmodels.ontario import cost

prices = oemp(load_da_prices("data/ieso_da_price_archive.csv"), fetch_lfda([2026]))
ga = load_ga("data/ieso_ga_archive.csv")
pdf = cost.site_pdf(meter_kwh, ontario_demand).value
bill = cost.bill_breakdown(meter_kwh, prices, ga, pdf)
print(bill.value[["month", "energy_dollars", "ga_class_a_dollars", "ga_class_b_dollars"]])
```

`examples/ontario_cost_attribution.py` prices three synthetic 1 MW-average sites
on real IESO data for August–September 2026, the months with both hourly prices
and actual GA:

| Site | GA share of Class A commodity bill | Class A vs Class B |
|---|---|---|
| 24/7 flat load | 34% | 21% cheaper |
| Same site, halving its load in the 5 peak hours of 2025 | 21% | 35% cheaper |
| Two-shift weekday plant (06:00–22:00) | 45% | 0.7% cheaper |

Halving load for **five hours** in June and August 2025 cut that site's
August–September 2026 commodity bill by 17%. A site that runs flat out on summer
afternoons gets almost nothing from Class A. And one kWh used in the 2024–25
peak hours (all on the late afternoon and evening of June 19, 2024) cost **$27 of GA** over the
nine months of its adjustment period with published actuals, about 480 times
the average hourly energy price of Aug–Oct 2026. GA was also *negative* from
December 2025 to February 2026, so Class A sites received GA credits that winter.

### The open IESO price archive

Since Market Renewal, IESO publishes hourly day-ahead prices only as daily files
kept for about 90 days, and monthly GA files for about 12 months. There is no
public yearly file. The same scheduled workflow that archives forecasts now
archives both, into `data/ieso_da_price_archive.csv` (from July 30, 2026) and
`data/ieso_ga_archive.csv` (from October 2025, every version, so estimates can
be compared with actuals).

## Measurement & verification (v0.4)

| Function | Model | What it does |
|---|---|---|
| `mv.fit_baseline()` | 6 | Fits 2P, 3P (cooling/heating), 4P and 5P change-point models of daily energy vs outdoor temperature, with a weekend shift and optional schedule flags, and picks the best by BIC |
| `mv.normalized_consumption()` | 4 | Consumption under a "normal" weather year, so two years can be compared without weather differences |
| `mv.cusum()` | 7 | Tracks actual use against the baseline and raises a drift alarm for sustained changes; reports the **minimum detectable change** for each building |
| `mv.savings()` | | Avoided energy in a reporting period |

Every baseline receipt checks the validity thresholds for daily models in BPA's
SEM M&V Reference Guide (which cites ASHRAE Guideline 14): CV(RMSE) < 20%,
net bias < 0.5%, R² > 0.5, |t| > 1.3 on weather slopes. All are adjustable.

**The self-check.** Each baseline is also run against its own baseline year. Nothing changed
within that year, so a drift alarm there means the model misses a recurring
pattern (an academic calendar, holidays, a seasonal process) even when CV(RMSE)
and R² look fine. Describe those days with `day_flags` and the baseline learns them:

```python
from ewmodels import mv

res = mv.fit_baseline(meter_kwh, outdoor_temp_c, day_flags=calendar)  # calendar: 0/1 columns by date
print(res.receipt.summary())   # includes the self-check result
drift = mv.cusum(res.value, next_year_kwh, next_year_temp, day_flags=next_calendar)
print(drift.value["min_detectable_pct"], drift.value["first_alarm_up"])
```

On every office in Building Data Genome 2 with full 2016 and 2017 data,
91 of 161 baselines passed the standard statistics. **31 of those 91 (34%)
failed the self-check**, leaving 60 trustworthy baselines. Passing the usual
accuracy tests is not enough on its own.

The drift alarm was tuned on synthetic buildings: 0 false alarms in 60 unchanged
building-years. On a clean baseline, a sustained 3% change is caught in about 27
days and an 8% change in about 10. Fractional savings uncertainty is not included yet.

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
| [0.5.0](https://github.com/enerwyse/ew-energy-models/releases/tag/v0.5.0) | 2026-10-28 | Class A cost attribution (Model 3): OEMP + Global Adjustment, Class A vs Class B, GA value of a peak-hour kWh; open IESO price and GA archive |
| [0.4.0](https://github.com/enerwyse/ew-energy-models/releases/tag/v0.4.0) | 2026-10-28 | M&V (Models 4, 6, 7): change-point baselines with validity checks and self-check, schedule flags, weather normalization, CUSUM drift alarm |
| [0.3.0](https://github.com/enerwyse/ew-energy-models/releases/tag/v0.3.0) | 2026-10-23 | Facility load analytics (Models 1, 2, 5); spike and meter-outage checks; Building Data Genome 2 loader |
| [0.2.0](https://github.com/enerwyse/ew-energy-models/releases/tag/v0.2.0) | 2026-10-22 | Ontario ICI toolkit (Models 9, 10): Peak Atlas, open 5CP benchmark, IESO cross-check, open forecast archive |

Full notes for each release are in [CHANGELOG.md](CHANGELOG.md).

**Next.** Candidates, to be chosen by user feedback: fractional savings
uncertainty for M&V, a longer ICI history (about 15 seasons), anomaly detection
on baseline residuals (Model 13), and battery sizing against ICI peak windows
(Model 17).

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
IESO data © Independent Electricity System Operator, used with attribution: the
archives in `data/` are republished from IESO public reports and are not part of
the PyPI package. Building Data Genome 2 data by Miller et al. (CC BY 4.0) is
fetched at runtime, not redistributed.
