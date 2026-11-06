# Changelog

All notable changes to `ew-energy-models`. Versions follow
[semantic versioning](https://semver.org); see "Versions" in the README.

## [Unreleased]

## [0.5.1] - 2026-11-06

### Fixed
- **ICI peak hours counted more than one hour per day.** IESO's five peak hours are the
  highest hour of each of the five highest-demand days. `ici.top_peak_hours()` took the
  five highest hours, so versions 0.2.0–0.5.0 could count two hours on one day and miss a
  peak day (2025–26: Jun 23 and Jun 24 counted twice; Jul 24 and Jul 28 missed). Now
  `one_per_day=True` by default; `one_per_day=False` reproduces the old behaviour.
  Affects the Peak Atlas, window coverage, the 5CP benchmark, PDFs and cost attribution.
- The old "25/25" cross-check was circular: it applied the same top-5 method to two IESO
  demand series. It is replaced by a check against IESO's official peak list.
- Season normalization (`normalize_seasons(method="threshold")`) uses the 5th peak *day*.

### Added
- `ewmodels.data.ieso_peaks`: parser for IESO's official Top-5-Peaks workbook (2010
  onward), `fetch_top5_peaks()` and `compare_with_official()`. Optional extra:
  `pip install "ew-energy-models[ieso]"` (openpyxl).
- `cost.peak_hour_value()` and `cost.site_pdf()` accept `official=`: published base
  periods use IESO's official peak hours and Total (MWh) as the PDF denominator.

### Corrected findings
- Withdrawn: "in 4 of 5 seasons all five peak hours fell on a single afternoon" and "a
  3–9 pm window was needed; the best 4-hour window caught 85%".
- Now (IESO official list): reproduced peaks match 19 of 20 hours for 2022–2026. Over 16
  seasons, 40 of 80 peak hours were HE17 EST; HE16–19 EST caught 84% (95% since 2022).
- Cost example: two-shift plant gains 0.2% from Class A (was 0.7%); other figures within
  one percentage point.

## [0.5.0] - 2026-10-28

Ontario Class A cost attribution (catalogue Model 3, re-scoped from time-of-use).

### Added
- `ewmodels.ontario.cost.peak_hour_value()`: GA dollars per MWh consumed in a base period's
  5 peak hours, over its adjustment period.
- `ewmodels.ontario.cost.site_pdf()`: Peak Demand Factor per base period from meter data.
- `ewmodels.ontario.cost.bill_breakdown()`: monthly commodity bill as Class A and as Class B
  (energy at OEMP, Global Adjustment including credits, optional delivery rates), with
  receipt checks for month coverage, GA availability and PDF availability.
- `ewmodels.data.ieso_prices`: parsers for IESO day-ahead Ontario zonal prices, hourly LFDA
  and monthly Global Adjustment; `oemp()`; archive loaders.
- Open IESO price archive: `scripts/archive_ieso_prices.py` and the scheduled workflow
  archive day-ahead prices (IESO keeps ~90 days) and every GA file version (~12 months),
  with gap alerts. History starts 2026-07-30 (prices) and 2025-10 (GA).
- `examples/ontario_cost_attribution.py`: three archetype sites priced on real IESO data.

## [0.4.0] - 2026-10-28

Measurement & verification (catalogue Models 4, 6, 7).

### Added
- `ewmodels.mv.fit_baseline()`: 2P, 3PC, 3PH, 4P and 5P change-point baselines on daily
  energy vs outdoor temperature, with a weekend shift, optional schedule flags
  (`day_flags`) and model selection by BIC.
- Baseline receipts check the BPA SEM M&V Reference Guide daily thresholds (citing ASHRAE
  Guideline 14): CV(RMSE) < 20%, net bias < 0.5%, R² > 0.5, |t| > 1.3 on weather slopes;
  coverage of a full year; and a **self-check**, the drift alarm run against the baseline
  year itself.
- `ewmodels.mv.normalized_consumption()` and `normal_year_temps()`: weather normalization.
- `ewmodels.mv.cusum()`: drift alarm combining a tabular CUSUM (k=1, h=8) with a
  noise-aware size filter; reports `min_detectable_pct`.
- `ewmodels.mv.savings()`: avoided energy in a reporting period.
- `ewmodels.data.bdg2.load_weather()`: BDG2 site weather loader.
- `examples/office_mv_baselines.py`: all of the above on every BDG2 office with full
  2016–2017 data.
- README: model status table, versions and versioning policy; this changelog.

### Not yet included
- Fractional savings uncertainty.

## [0.3.0] - 2026-10-23

Facility load analytics (catalogue Models 1, 2, 5).

### Added
- `ewmodels.load.load_profile()`: baseload (P10), typical (P50) and high load (P90),
  weekday and weekend hourly profiles, always-on share.
- `ewmodels.load.rank_peaks()`: top-N peaks (one per day by default), clustering by
  hour, weekday and month, and a coincidence factor against system peak hours.
- `ewmodels.load.load_factor()`: monthly load factor with coverage flags.
- Receipt checks for every load model: duplicates, negative readings, coverage,
  regular spacing, record length, spikes (max above 2× P99) and meter-outage runs
  (more than 5% of readings at or near zero).
- `ewmodels.data.bdg2`: Building Data Genome Project 2 loader with per-site timezone
  and daylight-saving handling.
- `examples/office_load_analytics.py`: all three models on every BDG2 office with a
  full year of 2017 data, reported by site.
- `scripts/inspect_bdg2_site.py`: meter-quality inspection for a BDG2 site.

### Changed
- Runbook: the virtual environment lives in `~/.venvs/ew-energy-models`.

### Fixed
- pandas deprecation warnings (`dayofweek`, `dayofyear`).

## [0.2.0] - 2026-10-22

Ontario ICI toolkit (catalogue Models 9, 10). First public release.

### Added
- `ewmodels.data.ieso`: IESO hourly demand and ICI Demand report loaders.
- `ewmodels.ontario.ici`: base periods, top-5 peak hours, Peak Demand Factor,
  GA charge, clean-energy PPA offset, curtailment simulation.
- `ewmodels.ontario.atlas`: Ontario Peak Atlas.
- `ewmodels.ontario.benchmark`: open 5CP benchmark with no look-ahead, curtailment
  windows, window coverage and a growth-removed stress test.
- `ewmodels.data.ieso_forecast` and `ewmodels.data.ieso_backfill`: Adequacy3 forecast
  parser and the open, self-healing IESO forecast archive with gap alerts.
- Evidence receipts (`ewmodels.receipts`) and resolution detection
  (`ewmodels.resolution`).
- Trusted Publishing to PyPI from GitHub releases.

[Unreleased]: https://github.com/enerwyse/ew-energy-models/compare/v0.5.1...HEAD
[0.5.1]: https://github.com/enerwyse/ew-energy-models/releases/tag/v0.5.1
[0.5.0]: https://github.com/enerwyse/ew-energy-models/releases/tag/v0.5.0
[0.4.0]: https://github.com/enerwyse/ew-energy-models/releases/tag/v0.4.0
[0.3.0]: https://github.com/enerwyse/ew-energy-models/releases/tag/v0.3.0
[0.2.0]: https://github.com/enerwyse/ew-energy-models/releases/tag/v0.2.0
