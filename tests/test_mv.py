import numpy as np
import pandas as pd
import pytest

from ewmodels import mv

TZ = "America/Toronto"


def weather(start="2025-01-01", days=365, seed=0):
    """Hourly outdoor temperature with a seasonal cycle and a daily swing (degC)."""
    idx = pd.date_range(start, periods=days * 24, freq="1h", tz=TZ)
    doy = idx.day_of_year.to_numpy()
    rng = np.random.default_rng(seed)
    daily_noise = np.repeat(rng.normal(0, 2.0, days), 24)
    t = 8 - 14 * np.cos((doy - 15) / 365 * 2 * np.pi) + 4 * np.sin((idx.hour.to_numpy() - 9) / 24 * 2 * np.pi)
    return pd.Series(t + daily_noise, index=idx, name="temp_c")


def building(temp, *, base=2000.0, weekend_drop=600.0, heat=40.0, cool=60.0, th=12.0, tc=18.0,
             noise=40.0, seed=1, step="1h"):
    """Daily kWh = base - weekend_drop*weekend + heat*max(th-T,0) + cool*max(T-tc,0),
    spread evenly over the day's intervals (5P truth)."""
    daily_t = temp.groupby(temp.index.normalize()).mean()
    wk = daily_t.index.day_of_week >= 5
    rng = np.random.default_rng(seed)
    kwh_day = (base - weekend_drop * wk + heat * np.maximum(th - daily_t, 0)
               + cool * np.maximum(daily_t - tc, 0) + rng.normal(0, noise, len(daily_t)))
    idx = pd.date_range(daily_t.index.min(), periods=len(daily_t) * int(pd.Timedelta("1D") / pd.Timedelta(step)),
                        freq=step, tz=TZ)
    per = pd.Timedelta("1D") / pd.Timedelta(step)
    return pd.Series(np.repeat(kwh_day.to_numpy() / per, per), index=idx.tz_convert("UTC"), name="kwh")


def test_recovers_5p_truth():
    t = weather()
    res = mv.fit_baseline(building(t), t, tz=TZ)
    b = res.value
    assert b.model == "5P"
    assert b.change_points_c[0] == pytest.approx(12, abs=1.5)
    assert b.change_points_c[1] == pytest.approx(18, abs=1.5)
    assert b.coefficients["heating_slope"] == pytest.approx(40, rel=0.15)
    assert b.coefficients["cooling_slope"] == pytest.approx(60, rel=0.15)
    assert b.coefficients["weekend_shift"] == pytest.approx(-600, rel=0.1)
    assert res.receipt.valid, res.receipt.summary()


def test_picks_3pc_for_cooling_only_building():
    t = weather()
    res = mv.fit_baseline(building(t, heat=0.0), t, tz=TZ)
    assert res.value.model in ("3PC", "5P")  # 5P with a ~zero heating slope may tie; BIC should prefer 3PC
    assert res.value.model == "3PC"


def test_resolution_agnostic():
    t = weather()
    a = mv.fit_baseline(building(t, step="1h"), t, tz=TZ).value
    b = mv.fit_baseline(building(t, step="15min"), t, tz=TZ).value
    assert a.model == b.model and a.change_points_c == b.change_points_c
    for k in a.coefficients:
        assert a.coefficients[k] == pytest.approx(b.coefficients[k], rel=1e-6)


def test_noisy_building_fails_validity():
    t = weather()
    res = mv.fit_baseline(building(t, noise=900.0), t, tz=TZ)
    failed = {c.name for c in res.receipt.checks if not c.passed}
    assert "cvrmse" in failed


def test_short_baseline_flagged():
    t = weather(days=120)
    res = mv.fit_baseline(building(t), t, tz=TZ)
    assert any(c.name == "baseline_covers_a_year" and not c.passed for c in res.receipt.checks)


def test_savings_detects_10pct_cut():
    t1, t2 = weather("2025-01-01"), weather("2026-01-01", seed=5)
    base = mv.fit_baseline(building(t1), t1, tz=TZ).value
    after = building(t2, seed=9) * 0.90
    s = mv.savings(base, after, t2, tz=TZ).value
    assert s["avoided_pct"] == pytest.approx(0.10, abs=0.015)


def test_cusum_alarm_after_step_change():
    t1, t2 = weather("2025-01-01"), weather("2026-01-01", seed=5)
    base = mv.fit_baseline(building(t1), t1, tz=TZ).value
    after = building(t2, seed=9)
    after[after.index >= pd.Timestamp("2026-07-01", tz=TZ)] *= 1.08  # equipment left on from July
    c = mv.cusum(base, after, t2, tz=TZ).value
    assert c["first_alarm_up"] is not None
    assert c["first_alarm_up"] >= pd.Timestamp("2026-07-01", tz=TZ)
    assert c["first_alarm_up"] <= pd.Timestamp("2026-07-31", tz=TZ)
    false_alarms = 0
    for seed in range(10):  # ten unchanged buildings, each monitored for a year
        steady = mv.cusum(base, building(t2, seed=100 + seed), t2, tz=TZ).value
        false_alarms += steady["first_alarm_up"] is not None or steady["first_alarm_down"] is not None
    assert false_alarms <= 1


def test_normalized_consumption_removes_weather():
    # Same building, a hot year and a mild year: actual totals differ, normalized ones match.
    t_mild, t_hot = weather("2025-01-01"), weather("2026-01-01") + 3.0
    b_mild = mv.fit_baseline(building(t_mild), t_mild, tz=TZ).value
    b_hot = mv.fit_baseline(building(t_hot, seed=3), t_hot, tz=TZ).value
    normal = mv.normal_year_temps(pd.concat([t_mild, t_hot]), tz=TZ, year=2025)
    n_mild = mv.normalized_consumption(b_mild, normal).value
    n_hot = mv.normalized_consumption(b_hot, normal).value
    assert n_hot == pytest.approx(n_mild, rel=0.03)


def test_noisy_baseline_raises_detection_floor_without_false_alarms():
    t1, t2 = weather("2025-01-01"), weather("2026-01-01", seed=5)
    noisy = mv.fit_baseline(building(t1, noise=200.0), t1, tz=TZ).value
    false_alarms = 0
    for seed in range(20):
        c = mv.cusum(noisy, building(t2, noise=200.0, seed=200 + seed), t2, tz=TZ).value
        false_alarms += c["first_alarm_up"] is not None or c["first_alarm_down"] is not None
    assert c["min_detectable_pct"] > 0.03
    assert false_alarms <= 1
    big = building(t2, noise=200.0, seed=7)
    big[big.index >= pd.Timestamp("2026-07-01", tz=TZ)] *= 1.15
    c = mv.cusum(noisy, big, t2, tz=TZ).value
    assert c["first_alarm_up"] is not None and c["first_alarm_up"] >= pd.Timestamp("2026-07-01", tz=TZ)


def campus(temp, seed=1):
    """A building that drops 25% during a May-August 'semester break' the weather
    model knows nothing about."""
    e = building(temp, seed=seed)
    local = e.index.tz_convert(TZ)
    brk = (local.month >= 5) & (local.month <= 8)
    e[brk] *= 0.75
    return e


def break_flags(year):
    days = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    return pd.Series(((days.month >= 5) & (days.month <= 8)).astype(int), index=days, name="semester_break")


def test_self_check_catches_unmodelled_schedule():
    t = weather()
    plain = mv.fit_baseline(campus(t), t, tz=TZ)
    sc = next(c for c in plain.receipt.checks if c.name == "self_check")
    assert not sc.passed
    assert plain.value.stats["self_check_first_alarm"] is not None


def test_day_flags_fix_it_and_carry_into_reporting():
    t1, t2 = weather("2025-01-01"), weather("2026-01-01", seed=5)
    res = mv.fit_baseline(campus(t1), t1, tz=TZ, day_flags=break_flags(2025))
    assert res.receipt.valid, res.receipt.summary()
    assert res.value.flags == ("semester_break",)
    assert res.value.coefficients["flag_semester_break_shift"] < 0
    # next year, same schedule, no real change: no drift, ~zero savings
    c = mv.cusum(res.value, campus(t2, seed=9), t2, tz=TZ, day_flags=break_flags(2026)).value
    assert c["first_alarm_up"] is None and c["first_alarm_down"] is None
    s = mv.savings(res.value, campus(t2, seed=9), t2, tz=TZ, day_flags=break_flags(2026)).value
    assert abs(s["avoided_pct"]) < 0.02


def test_steady_building_passes_self_check():
    t = weather()
    r = mv.fit_baseline(building(t), t, tz=TZ)
    assert next(c for c in r.receipt.checks if c.name == "self_check").passed
