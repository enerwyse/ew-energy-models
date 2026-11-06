"""IESO's official ICI peak hours and system-wide consumption.

IESO publishes one workbook, one sheet per base period (May 2010 onward):
"Top 5 Peaks: Hours & System-Wide Consumption". Each row is a peak hour:
rank, date, hour ending (EST), allocated quantity of energy withdrawn (AQEW),
embedded generation, energy storage injections (from 2018-19), and the Total (MWh)
that IESO uses as the Peak Demand Factor denominator.

This is the ground truth for peak hours: use it to check peaks reproduced from
demand data, and its Total (MWh) as the official PDF denominator.

Ranking basis: before May 2022 IESO ranked by AQEW; from the 2022-2023 base
period by Ontario Demand. In every base period the five hours are on five
different days.

Reading .xlsx needs openpyxl: pip install "ew-energy-models[ieso]".
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from ewmodels.data.ieso import EST

TOP5_URL = "https://ieso.ca/-/media/Files/IESO/settlements/Top-5-Peaks-Hours-and-System-Wide-Consumption.xlsx"

_YEAR = re.compile(r"(\d{4})\s*to\s*\w+\s*\d{1,2},\s*(\d{4})")


def _to_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return pd.Timestamp(str(v).replace("\xa0", " ").strip()).date()


def _norm(h) -> str:
    return re.sub(r"[^a-z]", "", str(h).lower())


def parse_top5_peaks(path_or_buffer) -> pd.DataFrame:
    """Parse IESO's Top-5-Peaks workbook into one row per peak hour.

    Columns: base_period ('2025-2026'), rank, interval_start (EST, hour start),
    hour_ending, aqew_mwh, embedded_mwh, storage_mwh, total_mwh.
    """
    try:
        import openpyxl
    except ImportError as e:  # pragma: no cover
        raise ImportError('reading IESO .xlsx needs openpyxl: pip install "ew-energy-models[ieso]"') from e
    wb = openpyxl.load_workbook(path_or_buffer, data_only=True, read_only=True)
    rows = []
    for ws in wb.worksheets:
        values = [r for r in ws.iter_rows(values_only=True) if any(v is not None for v in r)]
        title = " ".join(str(v) for v in values[0] if v) if values else ws.title
        m = _YEAR.search(title) or _YEAR.search(ws.title)
        if not m:
            continue
        bp = f"{m.group(1)}-{m.group(2)}"
        header_i = next(i for i, r in enumerate(values) if r and _norm(r[0]) in ("peak", "rank"))
        cols = {_norm(h): j for j, h in enumerate(values[header_i]) if h is not None}

        def col(*keys):
            return next((cols[k] for k in cols if any(k2 in k for k2 in keys)), None)

        c_date, c_he = col("date"), col("hourending")
        c_aqew, c_emb, c_sto, c_tot = col("allocated"), col("embedded"), col("storage"), col("total")
        for r in values[header_i + 1:]:
            if not isinstance(r[0], (int, float)):
                continue
            d, he = _to_date(r[c_date]), int(r[c_he])
            num = lambda c: float(r[c]) if c is not None and r[c] is not None else 0.0
            rows.append({"base_period": bp, "rank": int(r[0]),
                         "interval_start": pd.Timestamp(d, tz=EST) + pd.Timedelta(hours=he - 1),
                         "hour_ending": he, "aqew_mwh": num(c_aqew), "embedded_mwh": num(c_emb),
                         "storage_mwh": num(c_sto), "total_mwh": num(c_tot)})
    df = pd.DataFrame(rows).sort_values(["base_period", "rank"]).reset_index(drop=True)
    bad = df.groupby("base_period").size().loc[lambda s: s != 5]
    if len(bad):
        raise ValueError(f"expected 5 peaks per base period, got {bad.to_dict()}")
    return df


def fetch_top5_peaks(cache_path: str | Path | None = "~/.cache/ewmodels/ieso/Top-5-Peaks.xlsx",
                     refresh: bool = False) -> pd.DataFrame:
    """Download (or read the cached copy of) IESO's Top-5-Peaks workbook and parse it.
    IESO adds a sheet when each base period closes: pass refresh=True after May."""
    import io
    import urllib.request

    path = Path(cache_path).expanduser() if cache_path else None
    if path and path.exists() and not refresh:
        return parse_top5_peaks(path)
    with urllib.request.urlopen(TOP5_URL, timeout=60) as r:
        blob = r.read()
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    return parse_top5_peaks(io.BytesIO(blob))


def compare_with_official(reproduced: pd.DataFrame, official: pd.DataFrame) -> pd.DataFrame:
    """Per base period: how many reproduced peak hours appear in IESO's official list
    (order-independent), and which are missing / extra."""
    out = []
    for bp, off in official.groupby("base_period"):
        rep = reproduced[reproduced["base_period"] == bp]
        if rep.empty:
            continue
        a, b = set(rep["interval_start"]), set(off["interval_start"])
        out.append({"base_period": bp, "matched": len(a & b), "of": len(b),
                    "missing_from_ours": sorted(t.strftime("%Y-%m-%d HE%H") for t in
                                                (x + pd.Timedelta(hours=1) for x in b - a)),
                    "extra_in_ours": sorted(t.strftime("%Y-%m-%d HE%H") for t in
                                            (x + pd.Timedelta(hours=1) for x in a - b))})
    return pd.DataFrame(out)
