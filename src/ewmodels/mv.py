"""Measurement & verification: catalogue Models 6, 4 and 7.

    fit_baseline(energy, temp)          Model 6: ASHRAE-style change-point baselines
                                        (2P, 3PC, 3PH, 4P, 5P) on daily data
    savings(baseline, energy, temp)     avoided energy in a reporting period
    normalized_consumption(baseline, normal_temps)
                                        Model 4: consumption under "normal" weather
    cusum(baseline, energy, temp)       Model 7: cumulative deviation from baseline,
                                        with a tabular CUSUM drift alarm

Change-point model forms (T = daily mean outdoor temperature, degC):

    2P   E = b0 + b1*T
    3PC  E = b0 + bc*max(T - Tc, 0)                      cooling, bc >= 0
    3PH  E = b0 + bh*max(Th - T, 0)                      heating, bh >= 0
    4P   E = b0 + bh*max(Tcp - T, 0) + bc*max(T - Tcp, 0)   one change point, two slopes
    5P   E = b0 + bh*max(Th - T, 0) + bc*max(T - Tc, 0)     Th < Tc, flat deadband between

By default a weekend indicator shifts the intercept, so weekday/weekend
operation doesn't masquerade as weather sensitivity. Change points are found by
grid search, coefficients by least squares, and the model is chosen by BIC.

Validity checks (daily models) follow the thresholds in BPA's SEM M&V Reference
Guide, which cites ASHRAE Guideline 14: CV(RMSE) < 20%, net determination bias
< 0.5%, R^2 > 0.5, |t| > 1.3 on weather slopes. All thresholds are parameters;
use the ones your program requires.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd

from ewmodels.load import DEFAULT_TZ, _prepare
from ewmodels.receipts import Check, Result, make_receipt

MODELS = ("2P", "3PC", "3PH", "4P", "5P")
N_CHANGE_POINTS = {"2P": 0, "3PC": 1, "3PH": 1, "4P": 1, "5P": 2}
THRESHOLDS = {"cvrmse_max": 0.20, "ndbe_max": 0.005, "r2_min": 0.5, "t_min": 1.3}
THRESHOLD_SOURCE = ("BPA C&I SEM M&V Reference Guide (2022), recommended daily-model thresholds; "
                    "cites ASHRAE Guideline 14")


# ----------------------------------------------------------------- daily data

def _flags_frame(day_flags, tz: str) -> pd.DataFrame | None:
    """Normalize user schedule flags to a 0/1 DataFrame indexed by local midnight."""
    if day_flags is None:
        return None
    f = day_flags.to_frame(day_flags.name or "flag") if isinstance(day_flags, pd.Series) else day_flags.copy()
    idx = pd.DatetimeIndex(f.index)
    idx = idx.tz_localize(tz) if idx.tz is None else idx.tz_convert(tz)
    f.index = idx.normalize()
    return f.astype(float).groupby(level=0).max()


def daily_data(energy_kwh: pd.Series, temp_c: pd.Series, tz: str = DEFAULT_TZ,
               min_day_coverage: float = 0.95, day_flags: pd.DataFrame | pd.Series | None = None) -> pd.DataFrame:
    """Daily energy (kWh) and mean temperature (degC) on site-local calendar days.

    Days with less than `min_day_coverage` of their meter intervals, or fewer
    than 18 hours of temperature, are dropped rather than filled.

    day_flags: optional schedule calendar, one 0/1 column per kind of day (e.g.
    "holiday", "semester_break", "shutdown"), indexed by date. Days not listed
    count as 0. Each flag becomes its own intercept shift in the baseline.
    """
    e, _, res, _ = _prepare(energy_kwh, tz)
    if not isinstance(temp_c.index, pd.DatetimeIndex) or temp_c.index.tz is None:
        raise ValueError("temperature series needs a timezone-aware DatetimeIndex")
    t = temp_c.dropna().astype(float).tz_convert(tz)
    per_day = pd.Timedelta(days=1) / res
    eg = e.groupby(e.index.normalize())
    tg = t.groupby(t.index.normalize())
    df = pd.DataFrame({"kwh": eg.sum(), "n_intervals": eg.count(), "temp_c": tg.mean(), "temp_hours": tg.count()})
    ok = (df["n_intervals"] >= min_day_coverage * per_day) & (df["temp_hours"] >= 18)
    df = df[ok].dropna(subset=["kwh", "temp_c"])
    df["weekend"] = df.index.day_of_week >= 5
    out = df[["kwh", "temp_c", "weekend"]]
    f = _flags_frame(day_flags, tz)
    if f is not None:
        for c in f.columns:
            out[f"flag_{c}"] = f[c].reindex(out.index).fillna(0.0).to_numpy()
    return out


# ----------------------------------------------------------------- design matrices

def _design(model: str, T: np.ndarray, weekend: np.ndarray, cps: tuple, use_weekend: bool,
            F: np.ndarray | None = None, fnames: tuple = ()) -> tuple[np.ndarray, list[str]]:
    cols, names = [np.ones_like(T)], ["intercept"]
    if use_weekend:
        cols.append(weekend.astype(float))
        names.append("weekend_shift")
    for i, fn in enumerate(fnames):
        cols.append(F[:, i])
        names.append(f"flag_{fn}_shift")
    if model == "2P":
        cols.append(T); names.append("slope")
    elif model == "3PC":
        cols.append(np.maximum(T - cps[0], 0)); names.append("cooling_slope")
    elif model == "3PH":
        cols.append(np.maximum(cps[0] - T, 0)); names.append("heating_slope")
    elif model == "4P":
        cols.append(np.maximum(cps[0] - T, 0)); names.append("heating_slope")
        cols.append(np.maximum(T - cps[0], 0)); names.append("cooling_slope")
    elif model == "5P":
        cols.append(np.maximum(cps[0] - T, 0)); names.append("heating_slope")
        cols.append(np.maximum(T - cps[1], 0)); names.append("cooling_slope")
    return np.column_stack(cols), names


def _candidates(model: str, T: np.ndarray, min_share: float) -> list[tuple]:
    k = N_CHANGE_POINTS[model]
    if k == 0:
        return [()]
    grid = np.unique(np.round(np.quantile(T, np.linspace(min_share, 1 - min_share, 33)), 2))
    if k == 1:
        return [(c,) for c in grid]
    return [(a, b) for a, b in combinations(grid, 2) if b - a >= 1.0]


def _fit_one(model, T, y, weekend, use_weekend, min_share, F=None, fnames=()):
    best = None
    for cps in _candidates(model, T, min_share):
        X, names = _design(model, T, weekend, cps, use_weekend, F, fnames)
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        coef = dict(zip(names, beta))
        # Physical sign rules: 3P and 5P slopes must point "uphill" away from the
        # change point(s). 2P and 4P slopes are left free.
        if model in ("3PC", "5P") and coef["cooling_slope"] < 0:
            continue
        if model in ("3PH", "5P") and coef["heating_slope"] < 0:
            continue
        sse = float(np.sum((y - X @ beta) ** 2))
        if best is None or sse < best[0]:
            best = (sse, cps, beta, names, X)
    return best


# ----------------------------------------------------------------- drift detection

def _drift(d: pd.DataFrame, rmse: float, cv: float, k: float, h: float, min_effect: float,
           window_days: int) -> tuple[pd.DataFrame, object, object, float]:
    """Tabular CUSUM + practical-size filter on d["deviation_kwh"] / d["predicted_kwh"]."""
    z = d["deviation_kwh"] / rmse
    hi = lo = 0.0
    s_hi, s_lo = [], []
    for v in z:
        hi, lo = max(0.0, hi + v - k), max(0.0, lo - v - k)
        s_hi.append(hi)
        s_lo.append(lo)
    d = d.copy()
    d["cusum_kwh"] = d["deviation_kwh"].cumsum()
    d["cusum_up"], d["cusum_down"] = s_hi, s_lo
    roll = (d["deviation_kwh"].rolling(f"{window_days}D").sum()
            / d["predicted_kwh"].rolling(f"{window_days}D").sum())
    d["trailing_deviation_pct"] = roll
    # Smallest change this setup can flag: at least min_effect; at least ~3 standard
    # errors of a window_days average; and at least k baseline standard deviations,
    # because the CUSUM ignores daily shifts smaller than its allowance k.
    detectable = max(min_effect, 3 * cv / np.sqrt(window_days), k * cv)
    d["alarm_up"] = (d["cusum_up"] > h) & (roll >= detectable)
    d["alarm_down"] = (d["cusum_down"] > h) & (roll <= -detectable)
    first_up = d.index[d["alarm_up"]].min() if d["alarm_up"].any() else None
    first_down = d.index[d["alarm_down"]].min() if d["alarm_down"].any() else None
    return d, first_up, first_down, float(detectable)


DRIFT_DEFAULTS = {"k": 1.0, "h": 8.0, "min_effect": 0.03, "window_days": 30}


# ----------------------------------------------------------------- baseline

@dataclass
class Baseline:
    model: str
    change_points_c: tuple
    coefficients: dict
    use_weekend: bool
    stats: dict
    temp_range_c: tuple
    candidates: pd.DataFrame = field(repr=False)
    flags: tuple = ()

    def predict(self, temp_c, weekend=None, flags: np.ndarray | None = None) -> np.ndarray:
        T = np.asarray(temp_c, dtype=float)
        w = np.zeros_like(T, dtype=bool) if weekend is None else np.asarray(weekend, dtype=bool)
        F = np.zeros((len(T), len(self.flags))) if flags is None else np.asarray(flags, dtype=float)
        X, names = _design(self.model, T, w, self.change_points_c, self.use_weekend, F, self.flags)
        return X @ np.array([self.coefficients[n] for n in names])

    def predict_daily(self, daily: pd.DataFrame) -> pd.Series:
        F = np.column_stack([daily[f"flag_{fn}"].to_numpy() if f"flag_{fn}" in daily else np.zeros(len(daily))
                             for fn in self.flags]) if self.flags else None
        return pd.Series(self.predict(daily["temp_c"], daily["weekend"], F), index=daily.index, name="predicted_kwh")


def fit_baseline(
    energy_kwh: pd.Series,
    temp_c: pd.Series,
    tz: str = DEFAULT_TZ,
    *,
    models=MODELS,
    use_weekend: bool = True,
    min_segment_share: float = 0.10,
    thresholds: dict | None = None,
    day_flags: pd.DataFrame | pd.Series | None = None,
) -> Result[Baseline]:
    """Fit change-point baselines on daily energy vs temperature; pick the best by BIC.

    The receipt checks the chosen model against the validity thresholds (see module
    docstring), that the baseline covers about a year of weather, and a
    **self-check**: the drift alarm (see `cusum`) run over the baseline period
    itself. Nothing changed within that period, so an alarm means the model misses
    a recurring pattern (an academic calendar, holidays, a seasonal process) even
    if CV(RMSE) and R^2 look fine. Pass `day_flags` describing those days to fix it.
    """
    th = {**THRESHOLDS, **(thresholds or {})}
    _, _, res, data_checks = _prepare(energy_kwh, tz)
    d = daily_data(energy_kwh, temp_c, tz, day_flags=day_flags)
    if len(d) < 30:
        raise ValueError(f"only {len(d)} complete days; need at least 30 to fit a baseline")
    T, y, w = d["temp_c"].to_numpy(), d["kwh"].to_numpy(), d["weekend"].to_numpy()
    fnames = tuple(c[len("flag_"):] for c in d.columns if c.startswith("flag_"))
    F = d[[f"flag_{c}" for c in fnames]].to_numpy() if fnames else None
    n = len(y)
    rows, fits = [], {}
    for m in models:
        f = _fit_one(m, T, y, w, use_weekend, min_segment_share, F, fnames)
        if f is None:
            continue
        sse, cps, beta, names, X = f
        k = X.shape[1] + N_CHANGE_POINTS[m]  # regression terms + change points
        bic = n * np.log(sse / n) + k * np.log(n)
        fits[m] = (sse, cps, beta, names, X, k)
        rows.append({"model": m, "change_points_c": cps, "sse": sse, "params": k, "bic": bic})
    cand = pd.DataFrame(rows).sort_values("bic").reset_index(drop=True)
    best = cand.iloc[0]["model"]
    sse, cps, beta, names, X, p = fits[best]
    yhat = X @ beta
    ybar = float(y.mean())
    cvrmse = float(np.sqrt(sse / (n - p)) / ybar)
    ndbe = float((yhat.sum() - y.sum()) / y.sum())
    r2 = float(1 - sse / np.sum((y - ybar) ** 2))
    s2 = sse / (n - p)
    cov = s2 * np.linalg.pinv(X.T @ X)
    tstats = {nm: float(b / np.sqrt(cov[i, i])) if cov[i, i] > 0 else float("nan")
              for i, (nm, b) in enumerate(zip(names, beta))}
    stats = {"n_days": n, "params": int(p), "r2": r2, "cvrmse": cvrmse, "ndbe": ndbe,
             "rmse_kwh": float(np.sqrt(sse / (n - p))), "mean_daily_kwh": ybar, "t_stats": tstats}
    baseline = Baseline(best, tuple(float(c) for c in cps), dict(zip(names, map(float, beta))), use_weekend,
                        stats, (float(T.min()), float(T.max())), cand, fnames)
    insample = d.assign(predicted_kwh=yhat, deviation_kwh=y - yhat)
    _, s_up, s_down, s_det = _drift(insample, stats["rmse_kwh"], cvrmse, **DRIFT_DEFAULTS)
    stats["self_check_first_alarm"] = min([x for x in (s_up, s_down) if x is not None], default=None)

    span_days = (d.index.max() - d.index.min()).days + 1
    slope_names = [nm for nm in names if nm.endswith("slope")]
    checks = data_checks + [
        Check("baseline_covers_a_year", span_days >= 360 and n >= 300,
              f"{n} complete days spanning {span_days} days; a baseline should cover a full seasonal cycle"),
        Check("cvrmse", cvrmse < th["cvrmse_max"], f"CV(RMSE) {cvrmse:.1%} (limit {th['cvrmse_max']:.0%})"),
        Check("net_bias", abs(ndbe) < th["ndbe_max"], f"net determination bias {ndbe:.3%} (limit {th['ndbe_max']:.1%})"),
        Check("r2", r2 > th["r2_min"], f"R^2 {r2:.2f} (minimum {th['r2_min']})"),
    ]
    for nm in slope_names:
        checks.append(Check(f"t_stat_{nm}", abs(tstats[nm]) > th["t_min"],
                            f"|t| = {abs(tstats[nm]):.1f} (minimum {th['t_min']})"))
    sc = stats["self_check_first_alarm"]
    checks.append(Check("self_check", sc is None,
                        "no sustained drift against its own baseline period" if sc is None else
                        f"drifts against its own baseline period from {sc.date()} (>= {s_det:.1%} for weeks): "
                        "the model misses a recurring pattern; add day_flags for it"))
    caveats = [
        f"Validity thresholds: {THRESHOLD_SOURCE}. Confirm the thresholds your program requires.",
        "Daily model; statutory holidays are treated as their weekday.",
        "t-statistics treat change points as fixed, so they slightly overstate significance.",
    ]
    receipt = make_receipt("mv.fit_baseline", energy_kwh, checks=checks, caveats=caveats, parameters={
        "tz": tz, "models": list(models), "use_weekend": use_weekend, "thresholds": th,
        "chosen_model": best, "change_points_c": baseline.change_points_c, "day_flags": list(fnames)})
    return Result(baseline, receipt)


# ----------------------------------------------------------------- reporting period

def _reporting(baseline: Baseline, energy_kwh, temp_c, tz, day_flags=None):
    d = daily_data(energy_kwh, temp_c, tz, day_flags=day_flags)
    d["predicted_kwh"] = baseline.predict_daily(d)
    d["deviation_kwh"] = d["kwh"] - d["predicted_kwh"]
    lo, hi = baseline.temp_range_c
    outside = float(((d["temp_c"] < lo - 1) | (d["temp_c"] > hi + 1)).mean())
    check = Check("within_baseline_weather", outside <= 0.05,
                  f"{outside:.1%} of reporting days are >1 degC outside the baseline's {lo:.1f}–{hi:.1f} degC range")
    return d, check


def savings(baseline: Baseline, energy_kwh: pd.Series, temp_c: pd.Series, tz: str = DEFAULT_TZ,
            day_flags: pd.DataFrame | pd.Series | None = None) -> Result[dict]:
    """Avoided energy = baseline prediction under reporting-period weather (and schedule,
    if the baseline uses day_flags) minus actual use. Positive means the site used less
    than its baseline predicts."""
    d, wcheck = _reporting(baseline, energy_kwh, temp_c, tz, day_flags)
    pred, act = float(d["predicted_kwh"].sum()), float(d["kwh"].sum())
    value = {"avoided_kwh": pred - act, "avoided_pct": (pred - act) / pred if pred else float("nan"),
             "predicted_kwh": pred, "actual_kwh": act, "days": len(d), "daily": d}
    receipt = make_receipt("mv.savings", energy_kwh, checks=[wcheck], parameters={"baseline_model": baseline.model},
                           caveats=["No fractional savings uncertainty yet; planned for a later release.",
                                    "Assumes no non-routine changes (occupancy, floor area, equipment) "
                                    "between baseline and reporting periods."])
    return Result(value, receipt)


def normal_year_temps(temp_c: pd.Series, tz: str = DEFAULT_TZ, year: int = 2017) -> pd.Series:
    """A reference year of daily mean temperatures: the average for each calendar day
    (month, day) across all years in `temp_c`, laid onto the calendar of `year`."""
    t = temp_c.dropna().tz_convert(tz)
    daily = t.groupby(t.index.normalize()).mean()
    by_md = daily.groupby([daily.index.month, daily.index.day]).mean()
    cal = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D", tz=tz)
    vals = [by_md.get((d.month, d.day), np.nan) for d in cal]
    return pd.Series(vals, index=cal, name="normal_temp_c").interpolate(limit_direction="both")


def normalized_consumption(baseline: Baseline, normal_temps: pd.Series) -> Result[float]:
    """Model 4: annual consumption the baseline predicts under a normal-weather year.

    Comparing two years' baselines on the same normal year gives a weather-normalized
    change: (normalized_B - normalized_A) / normalized_A.
    """
    weekend = normal_temps.index.day_of_week >= 5
    total = float(baseline.predict(normal_temps.to_numpy(), weekend).sum())
    lo, hi = baseline.temp_range_c
    outside = float(((normal_temps < lo - 1) | (normal_temps > hi + 1)).mean())
    receipt = make_receipt("mv.normalized_consumption", normal_temps, parameters={"baseline_model": baseline.model},
                           checks=[Check("within_baseline_weather", outside <= 0.05,
                                         f"{outside:.1%} of normal-year days fall outside the baseline's weather range")],
                           caveats=["The normal year is only as representative as the weather years averaged; "
                                    "use 10+ years of station data where available."])
    return Result(total, receipt)


def cusum(baseline: Baseline, energy_kwh: pd.Series, temp_c: pd.Series, tz: str = DEFAULT_TZ,
          k: float = 1.0, h: float = 8.0, min_effect: float = 0.03, window_days: int = 30,
          day_flags: pd.DataFrame | pd.Series | None = None) -> Result[dict]:
    """Model 7: cumulative sum of (actual - predicted) daily energy, plus a drift alarm.

    An alarm needs two things at once:
    1. Statistical persistence: a two-sided tabular CUSUM (Page's method) on residuals
       standardized by the baseline RMSE exceeds h, with allowance k (both in standard
       deviations).
    2. Practical size: the trailing `window_days` deviation is at least
       `min_detectable_pct` = max(min_effect, 3 x CV(RMSE) / sqrt(window_days),
       k x CV(RMSE)). Noisier baselines can only flag larger changes, and the
       result says how large.

    Without (2), small persistent baseline errors (a fraction of a percent) trip the
    CUSUM on buildings that never changed; with it, alarms mean "something changed
    that is worth a look". Changes smaller than `min_effect` still show in the
    cumulative deviation, just without an alarm.

    Defaults (k=1, h=8) were chosen on synthetic buildings (tests/test_mv.py):
    0 false alarms in 60 unchanged building-years; on a clean baseline (CV(RMSE)
    ~2%) a sustained +3% change alarms in ~27 days, +5% in ~16, +8% in ~10; on a
    noisy one (CV(RMSE) ~8%) +8% in ~28 days and +15% in ~8."""
    d, wcheck = _reporting(baseline, energy_kwh, temp_c, tz, day_flags)
    d, first_up, first_down, detectable = _drift(d, baseline.stats["rmse_kwh"], baseline.stats["cvrmse"],
                                                 k, h, min_effect, window_days)
    value = {"daily": d, "first_alarm_up": first_up, "first_alarm_down": first_down,
             "min_detectable_pct": float(detectable),
             "total_deviation_kwh": float(d["deviation_kwh"].sum()),
             "deviation_pct": float(d["deviation_kwh"].sum() / d["predicted_kwh"].sum())}
    receipt = make_receipt("mv.cusum", energy_kwh, checks=[wcheck],
                           parameters={"baseline_model": baseline.model, "k": k, "h": h,
                                       "min_effect": min_effect, "window_days": window_days},
                           caveats=["Daily residuals are autocorrelated, so alarms are indicative, "
                                    "not a formal significance test."])
    return Result(value, receipt)
