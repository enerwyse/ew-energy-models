"""Open 5CP benchmark: score any peak-calling strategy the way a Class A
customer experiences it, as peak hours captured versus days spent curtailing.

Accuracy metrics (MAPE, AUC) don't price the real trade-off. A strategy that
calls every summer weekday captures every peak and wrecks operations; one that
calls three days may miss the one that mattered. This harness replays history
day by day, giving the strategy only information available before each day
plus a day-ahead forecast of that day's peak. The forecast is either synthetic
(true peak with Gaussian error) or real (e.g. archived IESO forecasts).

Scoring:
* Day level (window=None): a peak hour is captured if its day was called.
* Hour level (window=(start_hour, n_hours)): it must also fall inside the
  customer's curtailment window, in EST interval-start hours.

Stress testing: Ontario peak demand has grown every recent season, so any
rule anchored on last year's peaks looks better than it would in a flat year.
`normalize_seasons` removes that tailwind before benchmarking.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ewmodels.ontario.atlas import daily_peaks
from ewmodels.ontario.ici import N_PEAKS, base_period_label, top_peak_hours
from ewmodels.receipts import Check, Result

Window = tuple[int, int]  # (first interval-start hour in EST, number of hours)


@dataclass(frozen=True)
class DayContext:
    date: pd.Timestamp
    base_period: str
    forecast_peak_mw: float
    history: pd.DataFrame  # daily_peaks rows strictly before `date`


Strategy = Callable[[DayContext], bool]


def threshold_strategy(margin: float = 0.03, lookback_days: int = 365) -> Strategy:
    """Baseline: call the day if the forecast peak is within `margin` of the
    5th-highest daily peak seen over the trailing `lookback_days`."""

    def strategy(ctx: DayContext) -> bool:
        hist = ctx.history[ctx.history.index >= ctx.date - pd.Timedelta(days=lookback_days)]
        if len(hist) < N_PEAKS:
            return False
        threshold = hist["peak_mw"].nlargest(N_PEAKS).iloc[-1]
        return ctx.forecast_peak_mw >= (1 - margin) * threshold

    strategy.__name__ = f"threshold(margin={margin}, lookback={lookback_days}d)"
    return strategy


# ---------------------------------------------------------------- scoring core

def _in_window(ts: pd.Series, window: Window | None) -> pd.Series:
    if window is None:
        return pd.Series(True, index=ts.index)
    start, n = window
    return ts.dt.hour.between(start, start + n - 1)


def _score(peaks: pd.DataFrame, days: pd.DataFrame, complete_periods_only: bool = True,
           window: Window | None = None) -> pd.DataFrame:
    """Score a `days` frame carrying a boolean `called` column against the peaks."""
    rows = []
    for label, grp in peaks.groupby("base_period"):
        if complete_periods_only and not grp["complete"].iloc[0]:
            continue
        called_days = days.index[(days["base_period"] == label) & days["called"]]
        hit = grp["interval_start"].dt.normalize().isin(called_days) & _in_window(grp["interval_start"], window)
        captured = int(hit.sum())
        n_called = len(called_days)
        rows.append({
            "base_period": label,
            "days_called": n_called,
            "peak_hours_captured": captured,
            "capture_rate": captured / N_PEAKS,
            "days_per_captured_peak": (n_called / captured) if captured else float("inf"),
        })
    return pd.DataFrame(rows)


def rolling_threshold(days: pd.DataFrame, lookback_days: int = 365) -> pd.Series:
    """5th-highest daily peak over [date - lookback, date), with no look-ahead.
    Vectorized equivalent of the threshold used by `threshold_strategy`."""
    return days["peak_mw"].rolling(f"{lookback_days}D", closed="left", min_periods=N_PEAKS).apply(
        lambda x: np.partition(x, -N_PEAKS)[-N_PEAKS], raw=True)


def _forecasts(days: pd.DataFrame, forecast_error_pct: float, seed: int,
               forecast_peak_mw: pd.Series | None) -> np.ndarray:
    if forecast_peak_mw is not None:
        f = forecast_peak_mw.copy()
        f.index = pd.DatetimeIndex(f.index).tz_convert(days.index.tz).normalize()
        return f.reindex(days.index).to_numpy(dtype=float)  # NaN -> never called
    noise = np.random.default_rng(seed).normal(0.0, forecast_error_pct / 100.0, size=len(days))
    return days["peak_mw"].to_numpy() * (1.0 + noise)


# ---------------------------------------------------------------- public API

def evaluate(
    ontario_demand_mw: pd.Series,
    strategy: Strategy,
    *,
    forecast_error_pct: float = 0.0,
    seed: int = 0,
    forecast_peak_mw: pd.Series | None = None,
    window: Window | None = None,
    complete_periods_only: bool = True,
) -> Result[pd.DataFrame]:
    """Replay each base period and score the strategy.

    forecast_error_pct: std-dev of multiplicative Gaussian error on the true
        daily peak (0 = oracle forecast, an upper bound for the same rule).
    forecast_peak_mw: real daily-peak forecasts indexed by date; overrides the
        synthetic forecast. Days without a forecast are never called.
    window: (start_hour_est, n_hours) curtailment window for hour-level scoring.
    """
    peaks_res = top_peak_hours(ontario_demand_mw)
    peaks = peaks_res.value
    days = daily_peaks(ontario_demand_mw)
    forecasts = _forecasts(days, forecast_error_pct, seed, forecast_peak_mw)

    called = np.zeros(len(days), dtype=bool)
    for i, (date, row) in enumerate(days.iterrows()):
        if np.isnan(forecasts[i]):
            continue
        ctx = DayContext(date, row["base_period"], float(forecasts[i]), days.iloc[:i])
        called[i] = bool(strategy(ctx))
    days = days.assign(forecast_mw=forecasts, called=called)
    table = _score(peaks, days, complete_periods_only, window)

    receipt = peaks_res.receipt
    receipt.model = "ontario.benchmark.evaluate"
    receipt.parameters.update({
        "strategy": getattr(strategy, "__name__", repr(strategy)),
        "forecast": "provided" if forecast_peak_mw is not None else f"synthetic ±{forecast_error_pct}%",
        "seed": seed, "window": window,
    })
    receipt.checks.append(Check("periods_scored", len(table) > 0, f"{len(table)} base periods scored"))
    if forecast_peak_mw is not None and len(table):
        scored = days["base_period"].isin(table["base_period"])
        cov = float(np.isfinite(days.loc[scored, "forecast_mw"]).mean())
        receipt.checks.append(Check("forecast_coverage", cov >= 0.95,
                                    f"{cov:.1%} of scored days have a forecast"))
    receipt.caveats.append("Day-level scoring assumes curtailment spans the whole daily peak window."
                           if window is None else
                           f"Hour-level scoring: curtailment window {window[0]:02d}:00 EST for {window[1]} h.")
    return Result(table, receipt)


def tradeoff_curve(
    ontario_demand_mw: pd.Series,
    margins=(0.0, 0.01, 0.02, 0.03, 0.05, 0.08),
    forecast_errors_pct=(0.0, 1.0, 2.0, 3.0),
    seeds=range(20),
    lookback_days: int = 365,
    window: Window | None = None,
) -> pd.DataFrame:
    """Mean days-called and capture-rate across complete base periods for a
    grid of threshold margins x forecast-error levels (noise averaged over
    seeds), plus the worst season's capture rate. Vectorized; matches `evaluate`."""
    peaks = top_peak_hours(ontario_demand_mw).value
    days = daily_peaks(ontario_demand_mw)
    thr = np.nan_to_num(rolling_threshold(days, lookback_days).to_numpy(), nan=np.inf)
    out = []
    for err in forecast_errors_pct:
        for m in margins:
            runs = []
            for s in (seeds if err > 0 else [0]):
                f = _forecasts(days, err, s, None)
                runs.append(_score(peaks, days.assign(called=f >= (1 - m) * thr), window=window))
            t = pd.concat(runs)
            out.append({"forecast_error_pct": err, "margin": m,
                        "mean_days_called": t["days_called"].mean(),
                        "mean_capture_rate": t["capture_rate"].mean(),
                        "worst_season_capture": t.groupby("base_period")["capture_rate"].mean().min()})
    return pd.DataFrame(out)


def window_coverage(ontario_demand_mw: pd.Series, lengths=range(1, 9)) -> pd.DataFrame:
    """With perfect day selection, what share of peak hours does a fixed daily
    curtailment window of each length catch, at its best placement?

    Uses complete base periods only. Hours are EST interval starts; during
    summer, local (daylight) clock time is one hour later.
    """
    peaks = top_peak_hours(ontario_demand_mw).value
    peaks = peaks[peaks["complete"]]
    seasons = peaks["base_period"].nunique()
    rows = []
    for n in lengths:
        best = max(range(0, 25 - n), key=lambda s: (_in_window(peaks["interval_start"], (s, n)).sum(), -s))
        hit = _in_window(peaks["interval_start"], (best, n))
        full = int(hit.groupby(peaks["base_period"]).all().sum())
        rows.append({"hours": n, "best_start_est": best,
                     "local_daylight_window": f"{best + 1:02d}:00–{best + 1 + n:02d}:00",
                     "capture_rate": hit.mean(), "seasons_fully_covered": f"{full}/{seasons}"})
    return pd.DataFrame(rows)


def normalize_seasons(ontario_demand_mw: pd.Series, method: str = "mean",
                      reference: float | None = None) -> Result[pd.Series]:
    """Rescale each base period to remove year-over-year growth (stress test).

    method="mean":      equalize each season's average demand (removes load
                        growth; weather-driven peak variation is kept).
    method="threshold": equalize each season's 5th-peak (identical peak bars).
    reference: target level; default is the median across seasons.
    """
    s = ontario_demand_mw.dropna().sort_index()
    labels = pd.Series(s.index.map(base_period_label), index=s.index)
    if method == "mean":
        stat = s.groupby(labels).mean()
    elif method == "threshold":
        stat = s.groupby(labels).apply(lambda g: g.nlargest(N_PEAKS).iloc[-1])
    else:
        raise ValueError("method must be 'mean' or 'threshold'")
    # Partial seasons have biased stats (e.g. summer-only means), so they reuse the
    # factor of the latest complete season.
    from ewmodels.ontario.ici import base_period_bounds
    hours = s.groupby(labels).size()
    complete = {k for k in stat.index
                if hours[k] >= 0.999 * (base_period_bounds(k)[1] - base_period_bounds(k)[0]) / pd.Timedelta(hours=1)}
    if not complete:
        raise ValueError("need at least one complete base period to normalize")
    ref = float(stat[sorted(complete)].median()) if reference is None else reference
    factors = {}
    last = None
    for k in sorted(stat.index):
        if k in complete:
            last = ref / stat[k]
        factors[k] = last if last is not None else ref / stat[min(complete)]
    stat = pd.Series({k: ref / f for k, f in factors.items()})
    scaled = s * labels.map(factors).to_numpy()

    from ewmodels.receipts import make_receipt
    receipt = make_receipt("ontario.benchmark.normalize_seasons", s,
                           parameters={"method": method, "reference": ref,
                                       "scale_factors": {k: round(ref / v, 4) for k, v in stat.items()}})
    receipt.caveats.append("Counterfactual data for stress-testing strategies; not real demand.")
    return Result(scaled.rename(s.name), receipt)
