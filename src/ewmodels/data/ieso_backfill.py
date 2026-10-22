"""Version-aware IESO forecast archiving with backfill.

Observed IESO retention (Oct 2026): Adequacy3 keeps every published version
of each delivery date's forecast for roughly 45 days, then trims older dates
to their end-of-day version, which is published *during* the delivery day and
is useless as a forecast. So the forecast record has to be captured within
that window. Plain snapshots aren't enough.

`backfill()` reads the directory listing and, for each delivery date D, keeps
the latest version published before each of these cutoffs (EST):

    d2_1600   D-2 16:00   two days ahead
    d1_1000   D-1 10:00   day-ahead, morning
    d1_1600   D-1 16:00   day-ahead, afternoon (the standard horizon)
    d0_0000   D   00:00   last forecast before the day starts

Each chosen file's own <CreatedAt> is verified. Versions created on or after
the delivery day are rejected. Runs are idempotent, so daily runs heal any
missed days still inside IESO's retention window.
"""

from __future__ import annotations

import csv
import re
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ewmodels.data.ieso_forecast import parse_adequacy

LISTING_URL = "https://reports-public.ieso.ca/public/Adequacy3/"
FILE_URL = LISTING_URL + "{name}"
COLUMNS = ["delivery_date", "hour_ending", "forecast_mw", "created_at", "version", "cutoff", "retrieved_utc",
           "xml_path"]
CUTOFFS = {  # label -> offset from delivery-day midnight (EST)
    "d2_1600": timedelta(days=-2, hours=16),
    "d1_1000": timedelta(days=-1, hours=10),
    "d1_1600": timedelta(days=-1, hours=16),
    "d0_0000": timedelta(0),
}
_ROW = re.compile(
    r'href="(PUB_Adequacy3_(\d{8})_v(\d+)\.xml)"(?:(?!href=).)*?(\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2})',
    re.DOTALL,
)


@dataclass(frozen=True)
class ListedVersion:
    name: str
    delivery: date
    version: int
    listed_at: datetime  # naive, server-local listing time (verified later via CreatedAt)


def parse_listing(html: str) -> list[ListedVersion]:
    out = []
    for name, ymd, v, ts in _ROW.findall(html):
        out.append(ListedVersion(name, datetime.strptime(ymd, "%Y%m%d").date(), int(v),
                                 datetime.strptime(ts, "%d-%b-%Y %H:%M")))
    return out


def plan(listing: list[ListedVersion], done: set[tuple[str, int]] | None = None) -> list[tuple[ListedVersion, str]]:
    """Choose (version, cutoff_label) pairs to download, skipping versions already archived."""
    done = done or set()
    by_date: dict[date, list[ListedVersion]] = {}
    for lv in listing:
        by_date.setdefault(lv.delivery, []).append(lv)
    picks: dict[tuple[date, int], tuple[ListedVersion, list[str]]] = {}
    for d, versions in by_date.items():
        midnight = datetime.combine(d, datetime.min.time())
        for label, off in CUTOFFS.items():
            eligible = [v for v in versions if v.listed_at <= midnight + off]
            if not eligible:
                continue
            best = max(eligible, key=lambda v: (v.listed_at, v.version))
            key = (d, best.version)
            picks.setdefault(key, (best, []))[1].append(label)
    return [(lv, "+".join(labels)) for (d, ver), (lv, labels) in sorted(picks.items())
            if (d.isoformat(), ver) not in done]


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8")


def backfill(archive_csv: str | Path, *, fetch=_get, now_utc: datetime | None = None) -> dict:
    """Download chosen versions not yet archived and append them. Returns a summary."""
    path = Path(archive_csv)
    done: set[tuple[str, int]] = set()
    if path.exists():
        with path.open() as f:
            done = {(r["delivery_date"], int(r["version"])) for r in csv.DictReader(f)}
    listing = parse_listing(fetch(LISTING_URL))
    todo = plan(listing, done)
    now = (now_utc or datetime.now(timezone.utc).replace(tzinfo=None)).isoformat(timespec="seconds") + "Z"
    rows, rejected = [], []
    for lv, labels in todo:
        fc = parse_adequacy(fetch(FILE_URL.format(name=lv.name)))
        created = datetime.fromisoformat(fc.created_at[:19]) if fc.created_at else None
        if created is None or created >= datetime.combine(lv.delivery, datetime.min.time()):
            rejected.append(lv.name)  # published during/after the delivery day: not a forecast
            continue
        rows += [[lv.delivery.isoformat(), he, mw, fc.created_at, lv.version, labels, now, fc.xml_path]
                 for he, mw in fc.hourly_mw.items()]
    if rows:
        new = not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(COLUMNS)
            w.writerows(rows)
    return {"listed_versions": len(listing), "downloaded": len(todo), "rejected": rejected,
            "rows_appended": len(rows)}


def coverage_gaps(archive_csv: str | Path, start: date, end: date, cutoff: str = "d1_1600") -> list[date]:
    """Delivery dates in [start, end] with no archived forecast for `cutoff`."""
    have: set[str] = set()
    p = Path(archive_csv)
    if p.exists():
        with p.open() as f:
            have = {r["delivery_date"] for r in csv.DictReader(f) if cutoff in r["cutoff"].split("+")}
    out, d = [], start
    while d <= end:
        if d.isoformat() not in have:
            out.append(d)
        d += timedelta(days=1)
    return out
