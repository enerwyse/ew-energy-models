"""IESO public prices: day-ahead Ontario zonal price, LFDA, OEMP and Global Adjustment.

Since Market Renewal (May 1 2025) non-dispatchable loads are settled at the
Ontario Electricity Market Price (OEMP):

    OEMP(h) = DA_OZP(h) + LFDA(h)          $/MWh, per hour

    DA_OZP  day-ahead Ontario zonal price  PUB_DAHourlyOntarioZonalPrice_<YYYYMMDD>.xml
    LFDA    load forecast deviation adj.   PUB_HourlyLFDA_<YYYY>.csv (final ~10 business days later)

Global Adjustment monthly values come from PUB_GlobalAdjustment_<YYYYMM>.xml:
first estimate rate, second estimate ($ and $/MWh), actual ($ and $/MWh).
The $ figures are the total GA cost for the month (Class A and Class B).

Retention, observed Oct 2026: IESO keeps the daily DA price files for only
about 90 days and the GA files for about 12 months. There is no public yearly
file of post-renewal prices. So, like the demand-forecast archive, these must
be captured while they are still published: see scripts/archive_ieso_prices.py.

IESO reports use hour-ending EST. Series here are indexed by interval start in
fixed EST, the same convention as ewmodels.data.ieso.
"""

from __future__ import annotations

import csv
import io
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from ewmodels.data.ieso import EST, _cached

BASE = "https://reports-public.ieso.ca/public/"
DA_DIR = BASE + "DAHourlyOntarioZonalPrice/"
GA_DIR = BASE + "GlobalAdjustment/"
LFDA_URL = BASE + "HourlyLFDA/PUB_HourlyLFDA_{year}.csv"

DA_COLUMNS = ["delivery_date", "hour_ending", "zonal_price", "loss_price", "congestion_price", "flag",
              "version", "doc_revision", "created_at", "retrieved_utc"]
GA_COLUMNS = ["trade_month", "version", "doc_revision", "created_at", "first_estimate_rate", "second_estimate",
              "second_estimate_rate", "actual", "actual_rate", "retrieved_utc"]


# ---------------------------------------------------------------- XML helpers
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el: ET.Element, name: str) -> ET.Element | None:
    for e in el.iter():
        if _local(e.tag) == name:
            return e
    return None


def _text(el: ET.Element, name: str) -> str | None:
    e = _find(el, name)
    return e.text.strip() if e is not None and e.text and e.text.strip() else None


def _num(s: str | None) -> float:
    return float(s) if s not in (None, "") else float("nan")


# ---------------------------------------------------------------- parsers
@dataclass
class DayAheadPrices:
    delivery_date: date
    revision: int
    created_at: str | None
    rows: list[dict]  # hour_ending, zonal_price, loss_price, congestion_price, flag


def parse_da_ozp(xml_text: str) -> DayAheadPrices:
    """Parse one PUB_DAHourlyOntarioZonalPrice XML file."""
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    d = _text(root, "DeliveryDate")
    if d is None:
        raise ValueError("no DeliveryDate: IESO DA zonal price format may have changed")
    rows = []
    for e in root.iter():
        if _local(e.tag) != "HourlyPriceComponents":
            continue
        he = int(_text(e, "PricingHour"))
        if not 1 <= he <= 24:
            raise ValueError(f"PricingHour {he} outside 1..24")
        rows.append({"hour_ending": he, "zonal_price": _num(_text(e, "ZonalPrice")),
                     "loss_price": _num(_text(e, "LossPriceCapped")),
                     "congestion_price": _num(_text(e, "CongestionPriceCapped")),
                     "flag": _text(e, "Flag") or ""})
    if not rows:
        raise ValueError("no HourlyPriceComponents found")
    return DayAheadPrices(date.fromisoformat(d[:10]), int(_text(root, "DocRevision") or 0),
                          _text(root, "CreatedAt"), rows)


def parse_ga(xml_text: str) -> dict:
    """Parse one PUB_GlobalAdjustment XML file into a flat dict."""
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    month = _text(root, "TradeMonth")
    if month is None:
        raise ValueError("no TradeMonth: IESO GA format may have changed")
    return {"trade_month": month[:7], "revision": int(_text(root, "DocRevision") or 0),
            "created_at": _text(root, "CreatedAt"),
            "first_estimate_rate": _num(_text(root, "FirstEstimateRate")),
            "second_estimate": _num(_text(root, "SecondEstimate")),
            "second_estimate_rate": _num(_text(root, "SecondEstimateRate")),
            "actual": _num(_text(root, "Actual")),
            "actual_rate": _num(_text(root, "ActualRate"))}


def parse_lfda_csv(text: str) -> pd.DataFrame:
    """Parse a PUB_HourlyLFDA year file -> frame indexed by interval start (EST)
    with columns lfda ($/MWh, NaN where blank) and status."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    i = next((k for k, ln in enumerate(lines) if ln.lower().startswith("date,hour")), None)
    if i is None:
        raise ValueError("no 'Date,Hour,...' header: IESO LFDA format may have changed")
    df = pd.read_csv(io.StringIO("\n".join(lines[i:])))
    df.columns = [c.strip().lower() for c in df.columns]
    rate = next((c for c in df.columns if c.startswith("lfda")), None)
    if rate is None:
        raise ValueError("no LFDA rate column")
    hour = pd.to_numeric(df["hour"], errors="raise").astype(int)
    start = pd.to_datetime(df["date"]) + pd.to_timedelta(hour - 1, unit="h")
    out = pd.DataFrame({"lfda": pd.to_numeric(df[rate], errors="coerce"),
                        "status": df.get("status", pd.Series("", index=df.index)).astype(str).str.strip()})
    out.index = pd.DatetimeIndex(start).tz_localize(EST)
    out.index.name = "interval_start_est"
    return out[~out.index.duplicated(keep="last")].sort_index()


# ---------------------------------------------------------------- listings
_LISTING_ROW = re.compile(r'href="(PUB_[A-Za-z0-9]+_(\d{6}|\d{8})(?:_v(\d+))?\.(?:xml|csv))"')


def parse_listing(html: str) -> dict[str, list[tuple[int, str]]]:
    """{date key: [(version, file name), ...]} from an IESO directory listing.
    The plain dated file (no _vN) is listed with version 0; it is a copy of the latest."""
    out: dict[str, list[tuple[int, str]]] = {}
    for name, key, v in _LISTING_ROW.findall(html):
        out.setdefault(key, []).append((int(v) if v else 0, name))
    return out


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8-sig")


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def _archived(path: Path, key: str) -> set[tuple[str, int]]:
    if not path.exists():
        return set()
    with path.open() as f:
        return {(r[key], int(r["version"])) for r in csv.DictReader(f)}


def _append(path: Path, columns: list[str], rows: list[list]) -> None:
    if not rows:
        return
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(columns)
        w.writerows(rows)


# ---------------------------------------------------------------- archivers
# Archives are keyed by the file version in the name (_vN; 0 = unversioned file),
# not by DocRevision: the two don't always match (e.g. GA 2026-09 _v3 has DocRevision 1).

def _todo(listing: dict[str, list[tuple[int, str]]], have: set[tuple[str, int]], key_fmt) -> list:
    out = []
    for key, files in sorted(listing.items()):
        k = key_fmt(key)
        if k is None:
            continue
        versioned = [(v, n) for v, n in files if v > 0]
        for v, name in (versioned or files):
            if (k, v) not in have:
                out.append((k, v, name))
    return out


def archive_da_prices(archive_csv: str | Path, *, fetch=_get) -> dict:
    """Append every DA zonal price file version IESO still lists that isn't archived yet.
    Idempotent: re-runs only fetch new dates or new versions (administered-price republications)."""
    path = Path(archive_csv)
    have = _archived(path, "delivery_date")
    listing = parse_listing(fetch(DA_DIR))
    fmt = lambda k: f"{k[:4]}-{k[4:6]}-{k[6:]}" if len(k) == 8 else None
    todo = _todo(listing, have, fmt)
    now, rows = _now(), []
    for d, v, name in todo:
        p = parse_da_ozp(fetch(DA_DIR + name))
        if p.delivery_date.isoformat() != d:
            raise ValueError(f"{name}: DeliveryDate {p.delivery_date} does not match file name")
        rows += [[d, r["hour_ending"], r["zonal_price"], r["loss_price"], r["congestion_price"], r["flag"],
                  v, p.revision, p.created_at, now] for r in p.rows]
    _append(path, DA_COLUMNS, rows)
    return {"listed_dates": len(listing), "files_fetched": len(todo), "rows_appended": len(rows)}


def archive_ga(archive_csv: str | Path, *, fetch=_get) -> dict:
    """Append every GA monthly file version IESO still lists that isn't archived yet.
    All versions are kept, so estimates vs actuals can be studied later."""
    path = Path(archive_csv)
    have = _archived(path, "trade_month")
    listing = parse_listing(fetch(GA_DIR))
    fmt = lambda k: f"{k[:4]}-{k[4:]}" if len(k) == 6 else None
    todo = _todo(listing, have, fmt)
    now, rows = _now(), []
    for m, v, name in todo:
        g = parse_ga(fetch(GA_DIR + name))
        if g["trade_month"] != m:
            raise ValueError(f"{name}: TradeMonth {g['trade_month']} does not match file name")
        rows.append([m, v, g["revision"], g["created_at"], g["first_estimate_rate"], g["second_estimate"],
                     g["second_estimate_rate"], g["actual"], g["actual_rate"], now])
    _append(path, GA_COLUMNS, rows)
    return {"listed_months": len(listing), "files_fetched": len(todo), "rows_appended": len(rows)}


def da_price_gaps(archive_csv: str | Path, start: date, end: date) -> list[date]:
    """Delivery dates in [start, end] with fewer than 23 archived hours (DST-free EST: 24 expected)."""
    have: dict[str, set[int]] = {}
    p = Path(archive_csv)
    if p.exists():
        with p.open() as f:
            for r in csv.DictReader(f):
                have.setdefault(r["delivery_date"], set()).add(int(r["hour_ending"]))
    out = []
    for d in pd.date_range(start, end, freq="D").date:
        if len(have.get(d.isoformat(), ())) < 24:
            out.append(d)
    return out


# ---------------------------------------------------------------- loaders
def load_da_prices(archive_csv: str | Path) -> pd.Series:
    """Hourly DA Ontario zonal price ($/MWh), latest-created version per date, indexed by interval start (EST)."""
    df = pd.read_csv(archive_csv)
    last = df.groupby("delivery_date")["created_at"].transform("max")
    df = df[df["created_at"] == last]
    start = pd.to_datetime(df["delivery_date"]) + pd.to_timedelta(df["hour_ending"] - 1, unit="h")
    s = pd.Series(df["zonal_price"].to_numpy(float), index=pd.DatetimeIndex(start).tz_localize(EST),
                  name="da_ozp")
    s.index.name = "interval_start_est"
    return s[~s.index.duplicated(keep="last")].sort_index()


def load_ga(archive_csv: str | Path) -> pd.DataFrame:
    """Monthly GA values, latest-created version per month, indexed by trade month ('YYYY-MM')."""
    df = pd.read_csv(archive_csv)
    df = df.sort_values(["trade_month", "created_at", "version"]).groupby("trade_month").tail(1)
    return df.set_index("trade_month")[["version", "created_at", "first_estimate_rate", "second_estimate",
                                        "second_estimate_rate", "actual", "actual_rate"]]


def fetch_lfda(years, cache_dir: str | Path | None = "~/.cache/ewmodels/ieso") -> pd.DataFrame:
    """Download (or read cached) LFDA year files. A year is cached permanently once it has ended
    and its last LFDA values are final."""
    cache = Path(cache_dir).expanduser() if cache_dir else None
    frames = []
    for y in sorted(set(years)):
        path = cache / f"PUB_HourlyLFDA_{y}.csv" if cache else None
        final = date.today() > date(y + 1, 2, 1)
        frames.append(parse_lfda_csv(_cached(LFDA_URL.format(year=y), path, final)))
    df = pd.concat(frames)
    return df[~df.index.duplicated(keep="last")].sort_index()


def oemp(da_ozp: pd.Series, lfda: pd.DataFrame) -> pd.DataFrame:
    """OEMP = DA zonal price + LFDA, hourly. Columns: da_ozp, lfda, oemp, lfda_final.

    Hours whose LFDA is not yet published (or blank) get lfda = 0 and lfda_final = False,
    so the price is the DA price alone: callers should report how many such hours they used."""
    out = pd.DataFrame({"da_ozp": da_ozp.tz_convert(EST)})
    lf = lfda.reindex(out.index)
    out["lfda_final"] = lf["lfda"].notna() & lf["status"].str.lower().eq("final").fillna(False)
    out["lfda"] = lf["lfda"].fillna(0.0)
    out["oemp"] = out["da_ozp"] + out["lfda"]
    return out
