"""Interval-resolution detection.

Resolution is data, not configuration: utilities deliver 5-, 15- or 60-minute
intervals and we never assume which. Every model detects it and records it.
"""

from __future__ import annotations

import pandas as pd


def detect_resolution(index: pd.DatetimeIndex) -> pd.Timedelta:
    """Median spacing of a time index. Raises if fewer than two points."""
    if len(index) < 2:
        raise ValueError("need at least two timestamps to detect resolution")
    diffs = pd.Series(index.sort_values()).diff().dropna()
    return pd.Timedelta(diffs.median())


def describe_resolution(index: pd.DatetimeIndex) -> str | None:
    try:
        return str(detect_resolution(index))
    except ValueError:
        return None


def to_hourly_energy(energy: pd.Series) -> pd.Series:
    """Aggregate an interval *energy* series (kWh per interval) to hourly kWh.

    Requires a tz-aware index at hourly or finer resolution. Coarser data cannot
    be disaggregated honestly and raises.
    """
    if not isinstance(energy.index, pd.DatetimeIndex) or energy.index.tz is None:
        raise ValueError("energy series needs a timezone-aware DatetimeIndex")
    res = detect_resolution(energy.index)
    if res > pd.Timedelta(hours=1):
        raise ValueError(f"resolution {res} is coarser than hourly; cannot build hourly energy")
    if res == pd.Timedelta(hours=1):
        return energy.sort_index()
    return energy.resample("1h").sum(min_count=1)
