from datetime import timedelta

import pandas as pd
import pytest

from ewmodels.data import ieso_forecast as fc
from ewmodels.data.ieso import parse_demand_csv

ICI_SAMPLE = """\\\\Industrial Conservation Initiative Ontario Demand Report,,
\\\\Created at 2026-05-06 23:10:15,,
\\\\For 2025,,
Date,Hour,Ontario Demand
2025-05-01,2,12352
2025-05-01,3,12249
"""


def _xml(delivery="2026-07-14", created="2026-07-13T09:00:00", base=20000):
    hours = "".join(
        f"<Demand><DeliveryHour>{h}</DeliveryHour><EnergyMW>{base + 100 * h}</EnergyMW></Demand>"
        for h in range(1, 25))
    peak = "".join(  # instantaneous peak runs higher; must NOT be selected
        f"<Demand><DeliveryHour>{h}</DeliveryHour><EnergyMW>{base + 100 * h + 500}</EnergyMW></Demand>"
        for h in range(1, 25))
    supply = "".join(
        f"<Energy><DeliveryHour>{h}</DeliveryHour><EnergyMW>99999</EnergyMW></Energy>" for h in range(1, 25))
    return (f'<Document xmlns="http://www.ieso.ca/schema"><DocHeader><CreatedAt>{created}</CreatedAt></DocHeader>'
            f"<DocBody><DeliveryDate>{delivery}</DeliveryDate>"
            f"<ForecastSupply><Energies>{supply}</Energies></ForecastSupply>"
            f"<ForecastDemand><OntarioDemand><ForecastOntDemand>{hours}</ForecastOntDemand>"
            f"<PeakDemand>{peak}</PeakDemand></OntarioDemand>"
            f"</ForecastDemand></DocBody></Document>")


def test_ici_demand_without_market_column():
    df = parse_demand_csv(ICI_SAMPLE)
    assert list(df.columns) == ["ontario_demand_mw", "hour_ending"]
    assert df.iloc[0].ontario_demand_mw == 12352


def test_parse_adequacy_finds_demand_not_supply():
    f = fc.parse_adequacy(_xml())
    assert f.delivery_date.isoformat() == "2026-07-14"
    assert "ForecastOntDemand" in f.xml_path
    assert f.hourly_mw[1] == 20100 and len(f.hourly_mw) == 24


def test_parse_adequacy_rejects_no_demand():
    with pytest.raises(ValueError):
        fc.parse_adequacy("<Document><DocBody><DeliveryDate>2026-01-01</DeliveryDate></DocBody></Document>")


def test_archive_roundtrip(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_fetch(d):
        calls["n"] += 1
        return fc.parse_adequacy(_xml(delivery=d.isoformat(), created=f"{d - timedelta(days=1)}T09:00:00"))

    monkeypatch.setattr(fc, "fetch_forecast", fake_fetch)
    path = tmp_path / "archive.csv"
    assert fc.archive_snapshot(path, horizons_days=(1, 2)) == 48
    assert fc.archive_snapshot(path, horizons_days=(1, 2)) == 0  # idempotent
    peaks = fc.load_archive(path, lead_hours=0)
    assert len(peaks) == 2 and (peaks == 20000 + 2400).all()
    assert fc.load_archive(path, lead_hours=48).empty  # snapshots too late for 48 h lead
