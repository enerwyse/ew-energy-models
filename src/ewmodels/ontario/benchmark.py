"""Open 5CP benchmark — score any peak-calling strategy the way a Class A
customer experiences it: peak hours captured versus days spent curtailing.

Accuracy metrics (MAPE, AUC) don't price the real trade-off. A strategy that
calls every summer weekday captures every peak and wrecks operations; one that
calls three days may miss the one that mattered. This harness replays history
day by day, giving the strategy only information available before each day
plus a (possibly noisy) day-ahead forecast of that day's peak.

Scoring is at day granularity: a peak hour counts as captured if its day was
called. This assumes the customer curtails across the whole daily peak window.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ewmodels.ontario.atlas import daily_peaks
from ewmodels.ontario.ici import N_PEAKS, top_peak_hours
from ewmodels.receipts import Check, Result


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


def _score(peaks: pd.DataFrame, days: pd.DataFrame, complete_periods_only: bool = True) -> pd.DataFrame:
    """Score a `days` frame carrying a boolean `called` column against the peaks."""
    rows = []
    for label, grp in peaks.groupby("base_period"):
        if complete_periods_only and not grp["complete"].iloc[0]:
            continue
        peak_days = grp["interval_start"].dt.normalize()
        called_days = days.index[(days["base_period"] == label) & days["called"]]
        captured = int(peak_days.isin(called_days).sum())
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
    """5th-highest daily peak over [date - lookback, date) — no look-ahead.
    Vectorized equivalent of the threshold used by `threshold_strategy`."""
    return days["peak_mw"].rolling(f"{lookback_days}D", closed="left", min_periods=N_PEAKS).apply(
        lambda x: np.partition(x, -N_PEAKS)[-N_PEAKS], raw=True)


def evaluate(
    ontario_demand_mw: pd.Series,
    strategy: Strategy,
    *,
    forecast_error_pct: float = 0.0,
    seed: int = 0,
    complete_periods_only: bool = True,
) -> Result[pd.DataFrame]:
    """Replay each base period and score the strategy.

    forecast_error_pct: std-dev of multiplicative Gaussian error applied to the
        true daily peak to form the day-ahead forecast (0 = oracle forecast,
        an upper bound on any real forecaster using the same rule).
    """
    peaks_res = top_peak_hours(ontario_demand_mw)
    peaks = peaks_res.value
    days = daily_peaks(ontario_demand_mw)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, forecast_error_pct / 100.0, size=len(days))
    forecasts = days["peak_mw"].to_numpy() * (1.0 + noise)

    called = np.zeros(len(days), dtype=bool)
    for i, (date, row) in enumerate(days.iterrows()):
        ctx = DayContext(date, row["base_period"], float(forecasts[i]), days.iloc[:i])
        called[i] = bool(strategy(ctx))
    days = days.assign(forecast_mw=forecasts, called=called)

    table = _score(peaks, days, complete_periods_only)

    receipt = peaks_res.receipt
    receipt.model = "ontario.benchmark.evaluate"
    receipt.parameters.update({
        "strategy": getattr(strategy, "__name__", repr(strategy)),
        "forecast_error_pct": forecast_error_pct, "seed": seed,
    })
    receipt.checks.append(Check("periods_scored", len(table) > 0, f"{len(table)} base periods scored"))
    receipt.caveats.append("Day-level scoring: a captured day assumes curtailment spans the daily peak window.")
    return Result(table, receipt)


def tradeoff_curve(
    ontario_demand_mw: pd.Series,
    margins=(0.0, 0.01, 0.02, 0.03, 0.05, 0.08),
    forecast_errors_pct=(0.0, 1.0, 2.0, 3.0),
    seeds=range(20),
    lookback_days: int = 365,
) -> pd.DataFrame:
    """Mean days-called and capture-rate across complete base periods for a
    grid of threshold margins x forecast-error levels (noise averaged over
    seeds). Vectorized; results match `evaluate(threshold_strategy(...))`."""
    peaks = top_peak_hours(ontario_demand_mw).value
    days = daily_peaks(ontario_demand_mw)
    thr = rolling_threshold(days, lookback_days).to_numpy()
    actual = days["peak_mw"].to_numpy()
    out = []
    for err in forecast_errors_pct:
        for m in margins:
            runs = []
            for s in (seeds if err > 0 else [0]):
                noise = np.random.default_rng(s).normal(0.0, err / 100.0, size=len(days))
                called = (actual * (1.0 + noise)) >= (1 - m) * np.nan_to_num(thr, nan=np.inf)
                runs.append(_score(peaks, days.assign(called=called)))
            t = pd.concat(runs)
            out.append({"forecast_error_pct": err, "margin": m,
                        "mean_days_called": t["days_called"].mean(),
                        "mean_capture_rate": t["capture_rate"].mean()})
    return pd.DataFrame(out)
