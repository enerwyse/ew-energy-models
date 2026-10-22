"""IESO Ontario-demand forecasts (Adequacy3 reports) and a forward archive.

IESO publishes its hourly Ontario demand forecast in the Adequacy3 report
(https://reports-public.ieso.ca/public/Adequacy3/), one file per delivery date,
revised many times a day, and keeps only a rolling ~90-day window. Historical
day-ahead forecasts are therefore NOT publicly available. The only way to
build a forecast-vs-actual record is to snapshot forecasts as they are
published. That is what `archive_snapshot` does; run it daily (see
scripts/archive_ieso_forecast.py and the scheduled GitHub workflow).

The parser locates the Ontario demand forecast structurally (any hourly series
under a <ForecastDemand>-like container), so it tolerates minor schema
changes. Run `python -m ewmodels.data.ieso_forecast --inspect` against a
live file to see exactly which XML path it used.
"""

from __future__ import annotations

import argparse
import csv
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from ewmodels.data.ieso import EST

ADEQUACY_URL = "https://reports-public.ieso.ca/public/Adequacy3/PUB_Adequacy3_{ymd}.xml"
ARCHIVE_COLUMNS = ["delivery_date", "hour_ending", "forecast_mw", "created_at", "retrieved_utc", "xml_path"]
_VALUE_TAGS = ("EnergyMW", "MW", "Value", "Demand", "Quantity")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(el: ET.Element, name: str) -> str | None:
    for child in el:
        if _local(child.tag) == name and child.text and child.text.strip():
            return child.text.strip()
    return None


@dataclass
class DemandForecast:
    delivery_date: date
    created_at: str
    xml_path: str
    hourly_mw: dict[int, float]  # hour_ending (1..24, EST) -> MW


def _candidate_series(root: ET.Element) -> dict[str, dict[int, float]]:
    """Every hourly series inside a demand-forecast container, keyed by path."""
    found: dict[str, dict[int, float]] = {}

    def walk(el: ET.Element, path: list[str], in_demand: bool) -> None:
        name = _local(el.tag)
        in_demand = in_demand or ("Demand" in name and "Forecast" in "/".join(path + [name]))
        hour = _text(el, "DeliveryHour")
        if in_demand and hour is not None:
            for vt in _VALUE_TAGS:
                v = _text(el, vt)
                if v is not None:
                    key = "/".join(path[1:] + [name]) + f"[{vt}]"
                    try:
                        found.setdefault(key, {})[int(hour)] = float(v)
                    except ValueError:
                        pass
                    break
            return
        for child in el:
            walk(child, path + [name], in_demand)

    walk(root, [], False)
    return {k: v for k, v in found.items() if len(v) >= 20}


def parse_adequacy(xml_text: str) -> DemandForecast:
    root = ET.fromstring(xml_text)
    created = next((e.text.strip() for e in root.iter() if _local(e.tag) == "CreatedAt" and e.text), "")
    ddate = next((e.text.strip() for e in root.iter() if _local(e.tag) == "DeliveryDate" and e.text), None)
    if ddate is None:
        raise ValueError("Adequacy report has no DeliveryDate")
    cands = _candidate_series(root)
    if not cands:
        raise ValueError("no hourly Ontario demand forecast found; run --inspect and update the parser")
    # ICI peaks are set on hourly-average Ontario Demand, so we need the hourly
    # Ontario Demand forecast (ForecastOntDemand, equal to AverageDemand), NOT
    # PeakDemand (the instantaneous within-hour peak, which runs higher).
    # Verified against a live report (2026-10-22).
    for marker in ("/ForecastOntDemand/", "/AverageDemand/"):
        match = [k for k in cands if marker in f"/{k}"]
        if match:
            path = match[0]
            return DemandForecast(date.fromisoformat(ddate[:10]), created, path,
                                  dict(sorted(cands[path].items())))
    raise ValueError(f"no ForecastOntDemand/AverageDemand series found; candidates: {sorted(cands)}")


def fetch_forecast(delivery: date) -> DemandForecast:
    """Latest published forecast for a delivery date (its unversioned file)."""
    url = ADEQUACY_URL.format(ymd=delivery.strftime("%Y%m%d"))
    with urllib.request.urlopen(url, timeout=60) as r:
        return parse_adequacy(r.read().decode("utf-8"))


def archive_snapshot(archive_csv: str | Path, horizons_days=(0, 1, 2)) -> int:
    """Append the current forecasts for today+h (EST) to a CSV archive.

    Idempotent per (delivery_date, created_at): re-running without a new IESO
    revision adds nothing. Returns the number of rows appended.
    """
    path = Path(archive_csv)
    seen: set[tuple[str, str]] = set()
    if path.exists():
        with path.open() as f:
            seen = {(r["delivery_date"], r["created_at"]) for r in csv.DictReader(f)}
    today_est = datetime.now(timezone(timedelta(hours=-5))).date()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for h in horizons_days:
        fc = fetch_forecast(today_est + timedelta(days=h))
        if (fc.delivery_date.isoformat(), fc.created_at) in seen:
            continue
        rows += [[fc.delivery_date.isoformat(), he, mw, fc.created_at, now, fc.xml_path]
                 for he, mw in fc.hourly_mw.items()]
    if rows:
        new = not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(ARCHIVE_COLUMNS)
            w.writerows(rows)
    return len(rows)


def load_archive(archive_csv: str | Path, lead_hours: float = 24.0) -> pd.Series:
    """Daily *peak* forecast per delivery date, using for each date the latest
    snapshot created at least `lead_hours` before that date's midnight (EST).

    lead_hours=24 approximates a day-ahead forecast available the morning
    before; 0 gives the latest forecast issued before the day began.
    """
    df = pd.read_csv(archive_csv)
    created = pd.to_datetime(df["created_at"])  # IESO timestamps are naive EST
    df["created"] = created.dt.tz_localize(EST) if created.dt.tz is None else created.dt.tz_convert(EST)
    df["day"] = pd.to_datetime(df["delivery_date"]).dt.tz_localize(EST)
    df = df[df["created"] <= df["day"] - pd.Timedelta(hours=lead_hours)]
    if df.empty:
        return pd.Series(dtype=float, name="forecast_peak_mw")
    latest = df.loc[df.groupby("day")["created"].transform("max") == df["created"]]
    return latest.groupby("day")["forecast_mw"].max().rename("forecast_peak_mw")


def _main() -> None:
    ap = argparse.ArgumentParser(description="IESO Ontario demand forecast tools")
    ap.add_argument("--inspect", action="store_true", help="show the XML paths found in tomorrow's report")
    ap.add_argument("--archive", metavar="CSV", help="append current forecast snapshots to CSV")
    a = ap.parse_args()
    if a.inspect:
        d = datetime.now(timezone(timedelta(hours=-5))).date() + timedelta(days=1)
        url = ADEQUACY_URL.format(ymd=d.strftime("%Y%m%d"))
        with urllib.request.urlopen(url, timeout=60) as r:
            text = r.read().decode("utf-8")
        for k, v in _candidate_series(ET.fromstring(text)).items():
            print(f"{k}: {len(v)} hours, peak {max(v.values()):,.0f} MW")
        fc = parse_adequacy(text)
        print(f"\nSELECTED: {fc.xml_path}\ndelivery {fc.delivery_date}, created {fc.created_at}")
        print({h: round(v) for h, v in fc.hourly_mw.items()})
    if a.archive:
        print(f"appended {archive_snapshot(a.archive)} rows to {a.archive}")


if __name__ == "__main__":
    _main()
