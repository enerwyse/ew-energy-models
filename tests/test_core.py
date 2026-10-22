from pathlib import Path

import pandas as pd
import pytest

from ewmodels.data.ieso import EST, parse_demand_csv
from ewmodels.receipts import hash_data
from ewmodels.resolution import detect_resolution, to_hourly_energy

FIX = Path(__file__).parent / "fixtures" / "PUB_Demand_sample.csv"


def test_parse_ieso_hour_ending_to_interval_start():
    df = parse_demand_csv(FIX.read_text())
    assert list(df.columns[:2]) == ["market_demand_mw", "ontario_demand_mw"]
    assert df.index[0] == pd.Timestamp("2025-01-01 00:00", tz=EST)   # HE1 -> 00:00 start
    assert df.index[-1] == pd.Timestamp("2025-01-01 23:00", tz=EST)  # HE24 -> 23:00 start
    assert df["ontario_demand_mw"].iloc[0] == 13567
    assert str(df.index[0].utcoffset()) == "-1 day, 19:00:00"  # fixed UTC-5, no DST


def test_parse_rejects_unknown_format():
    with pytest.raises(ValueError):
        parse_demand_csv("foo,bar\n1,2\n")


def test_resolution_detection_and_hourly_rollup():
    idx = pd.date_range("2026-01-01", periods=8, freq="15min", tz="UTC")
    e = pd.Series(1.0, index=idx)
    assert detect_resolution(idx) == pd.Timedelta(minutes=15)
    hourly = to_hourly_energy(e)
    assert hourly.tolist() == [4.0, 4.0]


def test_hourly_rollup_refuses_coarser_data():
    idx = pd.date_range("2026-01-01", periods=4, freq="1D", tz="UTC")
    with pytest.raises(ValueError):
        to_hourly_energy(pd.Series(1.0, index=idx))


def test_hash_changes_with_data():
    s = pd.Series([1.0, 2.0])
    assert hash_data(s) != hash_data(s * 2)
    assert hash_data(s) == hash_data(s.copy())
