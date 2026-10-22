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
    missing = {"date", "hour", *_COLS} - set(df.columns)
    if missing:
        raise ValueError(f"IESO demand report missing columns: {sorted(missing)}")
    hour = pd.to_numeric(df["hour"], errors="raise").astype(int)
    if not hour.between(1, 24).all():
        raise ValueError("hour column outside 1..24 (expected hour-ending convention)")
    start = pd.to_datetime(df["date"]) + pd.to_timedelta(hour - 1, unit="h")
    out = df[list(_COLS)].rename(columns=_COLS).astype(float)
    out.index = pd.DatetimeIndex(start).tz_localize(EST)
    out.index.name = "interval_start_est"
    out["hour_ending"] = hour.values
    return out.sort_index()


def fetch_demand(years: Iterable[int], cache_dir: str | Path | None = "~/.cache/ewmodels/ieso") -> pd.DataFrame:
    """Download (or read cached) PUB_Demand files and return one hourly frame.

    The current year's file is always re-downloaded since IESO appends to it.
    """
    from datetime import date

    frames = []
    cache = Path(cache_dir).expanduser() if cache_dir else None
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
    for year in sorted(set(years)):
        path = cache / f"PUB_Demand_{year}.csv" if cache else None
        if path and path.exists() and year < date.today().year:
            text = path.read_text()
        else:
            with urllib.request.urlopen(DEMAND_URL.format(year=year), timeout=60) as r:
                text = r.read().decode("utf-8-sig")
            if path:
                path.write_text(text)
        frames.append(parse_demand_csv(text))
    df = pd.concat(frames)
    return df[~df.index.duplicated(keep="last")].sort_index()
