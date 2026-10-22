"""IESO public reports: hourly Ontario demand.

Source: https://reports-public.ieso.ca/public/Demand/ (PUB_Demand_<year>.csv).
IESO reports use Eastern Standard Time year-round (no daylight saving) and
label hours 1–24 as *hour ending*. We index each row by the interval *start*
in fixed EST (UTC-05:00), so hour-ending 1 on 2025-01-01 becomes
2025-01-01 00:00-05:00. Convert to UTC before joining with meter data.

Data is downloaded at runtime and cached locally; it is not redistributed in
this package. Cite the IESO as the source in anything you publish.
"""

from __future__ import annotations

import io
import urllib.request
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

EST = "Etc/GMT+5"  # fixed UTC-05:00 (POSIX sign convention is inverted)
DEMAND_URL = "https://reports-public.ieso.ca/public/Demand/PUB_Demand_{year}.csv"
ICI_DEMAND_URL = "https://reports-public.ieso.ca/public/ICIDemand/PUB_ICIDemand_{year}.csv"
_COLS = {"market demand": "market_demand_mw", "ontario demand": "ontario_demand_mw"}


def parse_demand_csv(text: str) -> pd.DataFrame:
    """Parse a PUB_Demand CSV into an hourly frame indexed by interval start (EST)."""
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("\\")]
    header_i = next(
        (i for i, ln in enumerate(lines) if ln.lower().startswith("date") and "hour" in ln.lower()),
        None,
    )
    if header_i is None:
        raise ValueError("no 'Date,Hour,...' header found; IESO report format may have changed")
    df = pd.read_csv(io.StringIO("\n".join(lines[header_i:])))
    df.columns = [c.strip().lower() for c in df.columns]
    missing = {"date", "hour", "ontario demand"} - set(df.columns)
    if missing:
        raise ValueError(f"IESO demand report missing columns: {sorted(missing)}")
    cols = [c for c in _COLS if c in df.columns]  # Market Demand is absent in ICIDemand files
    hour = pd.to_numeric(df["hour"], errors="raise").astype(int)
    if not hour.between(1, 24).all():
        raise ValueError("hour column outside 1..24 (expected hour-ending convention)")
    start = pd.to_datetime(df["date"]) + pd.to_timedelta(hour - 1, unit="h")
    out = df[cols].rename(columns=_COLS).astype(float)
    out.index = pd.DatetimeIndex(start).tz_localize(EST)
    out.index.name = "interval_start_est"
    out["hour_ending"] = hour.values
    return out.sort_index()


def _fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8-sig")


def _cached(url: str, path: Path | None, final: bool) -> str:
    if path and final and path.exists():
        return path.read_text()
    text = _fetch_text(url)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return text


def fetch_demand(years: Iterable[int], cache_dir: str | Path | None = "~/.cache/ewmodels/ieso") -> pd.DataFrame:
    """Download (or read cached) calendar-year PUB_Demand files as one hourly frame.

    The current year's file is always re-downloaded since IESO appends to it.
    """
    from datetime import date

    cache = Path(cache_dir).expanduser() if cache_dir else None
    frames = []
    for year in sorted(set(years)):
        path = cache / f"PUB_Demand_{year}.csv" if cache else None
        frames.append(parse_demand_csv(_cached(DEMAND_URL.format(year=year), path, year < date.today().year)))
    df = pd.concat(frames)
    return df[~df.index.duplicated(keep="last")].sort_index()


def fetch_ici_demand(base_period_years: Iterable[int],
                     cache_dir: str | Path | None = "~/.cache/ewmodels/ieso") -> pd.DataFrame:
    """IESO's ICI Ontario Demand reports — the demand series published for ICI
    peak determination. File year Y covers base period May 1 Y – Apr 30 Y+1.

    Use this to cross-check peaks reproduced from PUB_Demand. A file is cached
    permanently only once its base period has closed (after May 31 of Y+1).
    """
    from datetime import date

    cache = Path(cache_dir).expanduser() if cache_dir else None
    today = date.today()
    frames = []
    for y in sorted(set(base_period_years)):
        path = cache / f"PUB_ICIDemand_{y}.csv" if cache else None
        final = today > date(y + 1, 5, 31)
        frames.append(parse_demand_csv(_cached(ICI_DEMAND_URL.format(year=y), path, final)))
    df = pd.concat(frames)
    return df[~df.index.duplicated(keep="last")].sort_index()
