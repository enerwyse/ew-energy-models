import numpy as np
import pandas as pd
import pytest

from ewmodels import load

TZ = "America/Toronto"


def site(freq="1h", start="2026-01-05", days=28, base=100.0, extra=400.0):
    """Synthetic office: 100 kW always on, +400 kW on weekdays 09:00-16:59 local.
    Returns interval energy (kWh) in UTC, like utility data."""
    idx = pd.date_range(start, periods=int(days * 24 * pd.Timedelta("1h") / pd.Timedelta(freq)), freq=freq, tz=TZ)
    on = (idx.day_of_week < 5) & (idx.hour >= 9) & (idx.hour < 17)
    kw = base + extra * on
    step_h = pd.Timedelta(freq) / pd.Timedelta("1h")
    return pd.Series(kw * step_h, index=idx.tz_convert("UTC"), name="kwh")


@pytest.mark.parametrize("freq", ["1h", "15min"])
def test_profile_known_shape(freq):
    res = load.load_profile(site(freq), tz=TZ)
    s = res.value["summary"]
    assert s["baseload_kw"] == pytest.approx(100)
    assert s["max_kw"] == pytest.approx(500)
    # 40 of 168 weekly hours are "on": energy = 100*168 + 400*40 per week
    assert s["always_on_share"] == pytest.approx(100 * 168 / (100 * 168 + 400 * 40))
    prof = res.value["profile"]
    assert prof.loc[12, ("weekday", "p50")] == pytest.approx(500)
    assert prof.loc[12, ("weekend", "p50")] == pytest.approx(100)
    assert prof.loc[3, ("weekday", "p90")] == pytest.approx(100)
    assert res.receipt.valid


def test_resolution_agnostic_profile():
    a = load.load_profile(site("1h")).value["summary"]
    b = load.load_profile(site("15min")).value["summary"]
    for k in ("baseload_kw", "typical_kw", "max_kw", "always_on_share", "total_kwh"):
        assert a[k] == pytest.approx(b[k])


def test_requires_tz_aware():
    s = site().tz_localize(None)
    with pytest.raises(ValueError):
        load.load_profile(s)


def test_receipt_flags_gaps_and_negatives():
    s = site()
    gappy = s.iloc[::2]  # half the intervals -> irregular and low coverage
    r = load.load_profile(pd.concat([gappy, s.iloc[-3:]])).receipt
    assert not r.valid
    neg = s.copy()
    neg.iloc[5] = -1
    assert not load.load_profile(neg).receipt.valid


def test_rank_peaks_one_per_day_and_clustering():
    s = site()
    spike_t = pd.Timestamp("2026-01-14 15:00", tz=TZ)
    s.loc[spike_t.tz_convert("UTC")] = 900.0
    res = load.rank_peaks(s, n=5, tz=TZ)
    p = res.value["peaks"]
    assert p.iloc[0]["demand_kw"] == pytest.approx(900)
    assert p.iloc[0]["interval_start"] == spike_t
    assert p["interval_start"].dt.normalize().is_unique  # one per day
    c = res.value["clustering"]
    assert c["share_on_weekdays"] == 1.0


def test_coincidence_with_system_peaks():
    s = site("15min")
    on_peak = pd.DatetimeIndex([pd.Timestamp("2026-01-07 14:00", tz=TZ)])   # site at 500 kW
    off_peak = pd.DatetimeIndex([pd.Timestamp("2026-01-07 19:00", tz=TZ)])  # site at 100 kW
    hi = load.rank_peaks(s, system_peak_hours=on_peak).value["coincidence"]
    lo = load.rank_peaks(s, system_peak_hours=off_peak).value["coincidence"]
    assert hi["coincidence_factor"] == pytest.approx(1.0)
    assert lo["coincidence_factor"] == pytest.approx(0.2)
    missing = pd.DatetimeIndex([pd.Timestamp("2025-07-01 16:00", tz=TZ)])
    assert not load.rank_peaks(s, system_peak_hours=missing).receipt.valid


def test_load_factor_monthly():
    s = site(days=59, start="2026-01-01")  # Jan complete, Feb complete (28 days)
    lf = load.load_factor(s, tz=TZ).value
    jan = lf.iloc[0]
    assert jan["peak_kw"] == pytest.approx(500)
    assert jan["load_factor"] == pytest.approx(jan["kwh"] / (500 * 31 * 24))
    assert bool(jan["complete"])
    part = load.load_factor(site(days=10, start="2026-03-01")).value.iloc[0]
    assert not bool(part["complete"])  # 10 of 31 days


def test_load_factor_ignores_gaps_in_average():
    s = site(days=31, start="2026-01-01")
    full = load.load_factor(s).value.iloc[0]
    gappy = load.load_factor(s.drop(s.index[(s.index.day == 3)])).value.iloc[0]
    assert gappy["coverage"] < full["coverage"]
    assert np.isfinite(gappy["load_factor"])


def test_spike_flagged_in_receipt():
    s = site()
    assert load.load_profile(s).receipt.valid
    s.iloc[100] = 5000.0  # one bad reading, 10x the real peak
    r = load.load_profile(s).receipt
    assert not r.valid
    assert any(c.name == "no_extreme_spikes" and not c.passed for c in r.checks)


def test_zero_runs_flagged_in_receipt():
    s = site()
    s.iloc[: len(s) // 10] = 0.0  # 10% of the record reads zero: a meter outage
    r = load.load_profile(s).receipt
    assert any(c.name == "few_zero_readings" and not c.passed for c in r.checks)


def test_near_zero_runs_flagged_too():
    s = site()
    s.iloc[: len(s) // 10] = 0.01  # outage logged as a tiny non-zero value
    r = load.load_profile(s).receipt
    assert any(c.name == "few_zero_readings" and not c.passed for c in r.checks)
