import numpy as np
import pandas as pd
import pytest

from ewmodels.data.ieso import EST

# Hand-placed system peaks for base period 2023-2024 (EST interval starts).
PLANTED = [
    ("2023-07-27 16:00", 24500.0),
    ("2023-08-14 17:00", 24400.0),
    ("2023-07-05 16:00", 24300.0),
    ("2023-07-05 17:00", 24250.0),  # second peak hour on the same day
    ("2023-09-05 15:00", 24200.0),
]


@pytest.fixture
def demand() -> pd.Series:
    """Two complete base periods (2023-05-01 .. 2025-04-30) of synthetic hourly
    Ontario demand, smooth enough that only the planted hours exceed 22,000 MW."""
    idx = pd.date_range("2023-05-01", "2025-05-01", freq="1h", inclusive="left", tz=EST)
    h = idx.hour.to_numpy()
    doy = idx.dayofyear.to_numpy()
    rng = np.random.default_rng(42)
    base = 15000 + 2500 * np.sin((h - 6) / 24 * 2 * np.pi) + 1500 * np.cos((doy - 200) / 365 * 2 * np.pi)
    s = pd.Series(base + rng.normal(0, 100, len(idx)), index=idx, name="ontario_demand_mw")
    for ts, mw in PLANTED:
        s[pd.Timestamp(ts, tz=EST)] = mw
    # second period: a single planted top-5 set
    for i, ts in enumerate(["2024-06-18 16:00", "2024-06-19 16:00", "2024-07-09 17:00",
                            "2024-07-15 16:00", "2024-08-27 16:00"]):
        s[pd.Timestamp(ts, tz=EST)] = 23900.0 - 50 * i
    return s
