import pandas as pd
import pytest

from ewmodels.data.ieso import EST
from ewmodels.ontario import atlas, benchmark, ici

from .conftest import PLANTED


def test_base_period_labels():
    assert ici.base_period_label(pd.Timestamp("2025-04-30 23:00", tz=EST)) == "2024-2025"
    assert ici.base_period_label(pd.Timestamp("2025-05-01 00:00", tz=EST)) == "2025-2026"
    # 2025-05-01 03:30 UTC is still April 30 in EST
    assert ici.base_period_label(pd.Timestamp("2025-05-01 03:30", tz="UTC")) == "2024-2025"


def test_top_peak_hours_finds_planted(demand):
    res = ici.top_peak_hours(demand)
    p = res.value
    first = p[p.base_period == "2023-2024"].sort_values("rank")
    assert [str(t) for t in first.interval_start.dt.strftime("%Y-%m-%d %H:%M")] == [t for t, _ in PLANTED]
    assert first["complete"].all()
    assert res.receipt.valid
    assert res.receipt.resolution == "0 days 01:00:00"


def _customer(demand, kw=1000.0):
    return pd.Series(kw, index=demand.index.tz_convert("UTC"))  # flat 1 MW = 1000 kWh/h


def test_pdf_arithmetic(demand):
    peaks = ici.top_peak_hours(demand).value
    hours = pd.DatetimeIndex(peaks[peaks.base_period == "2023-2024"].interval_start)
    system = demand.reindex(hours)  # MW over 1 h == MWh
    res = ici.peak_demand_factor(_customer(demand), hours, system, denominator_source="ontario_demand")
    expected = 5 * 1.0 / sum(mw for _, mw in PLANTED)
    assert res.value == pytest.approx(expected)
    assert res.receipt.valid
    assert any("not the IESO-published" in c for c in res.receipt.caveats)


def test_pdf_invalid_when_meter_gap(demand):
    peaks = ici.top_peak_hours(demand).value
    hours = pd.DatetimeIndex(peaks[peaks.base_period == "2023-2024"].interval_start)
    cust = _customer(demand).drop(hours[0].tz_convert("UTC"))
    res = ici.peak_demand_factor(cust, hours, demand.reindex(hours))
    assert not res.receipt.valid


def test_ppa_offset_floors_at_zero(demand):
    peaks = ici.top_peak_hours(demand).value
    hours = pd.DatetimeIndex(peaks[peaks.base_period == "2023-2024"].interval_start)
    res = ici.peak_demand_factor(_customer(demand), hours, demand.reindex(hours), ppa_offset_kwh=5000.0)
    assert res.value == 0.0


def test_curtailment_savings(demand):
    peaks = ici.top_peak_hours(demand).value
    hours = pd.DatetimeIndex(peaks[peaks.base_period == "2023-2024"].interval_start)
    out = ici.simulate_curtailment(_customer(demand), hours, demand.reindex(hours),
                                   curtail_kw=400, adjustment_period_ga_dollars=1e9).value
    assert out["scenario_pdf"] < out["baseline_pdf"]
    assert out["scenario_pdf"] == pytest.approx(out["baseline_pdf"] * 0.6, rel=1e-3)
    partial = ici.simulate_curtailment(_customer(demand), hours, demand.reindex(hours), curtail_kw=400,
                                       adjustment_period_ga_dollars=1e9, hit_hours=hours[:2]).value
    assert 0 < partial["savings_dollars"] < out["savings_dollars"]


def test_atlas_summary(demand):
    a = atlas.peak_atlas(demand).value
    row = a["summary"].set_index("base_period").loc["2023-2024"]
    assert row.threshold_mw == 24200.0
    assert row.distinct_peak_days == 4  # two peak hours fell on 2023-07-05
    assert row.days_within_1pct == 4
    assert a["by_month"].sum() == 10


def test_benchmark_oracle_vs_never(demand):
    never = benchmark.evaluate(demand, lambda ctx: False).value
    assert (never.peak_hours_captured == 0).all()
    always = benchmark.evaluate(demand, lambda ctx: True).value
    assert (always.capture_rate == 1.0).all()
    rule = benchmark.evaluate(demand, benchmark.threshold_strategy(margin=0.10)).value
    assert len(rule) == 2 and (rule.days_called < always.days_called).all()


def test_benchmark_does_not_leak_future(demand):
    seen = []

    def spy(ctx):
        assert (ctx.history.index < ctx.date).all()
        seen.append(ctx.date)
        return False

    benchmark.evaluate(demand, spy)
    assert len(seen) > 700


def test_vectorized_curve_matches_replay(demand):
    for m, err, seed in [(0.10, 0.0, 0), (0.03, 2.0, 7)]:
        slow = benchmark.evaluate(demand, benchmark.threshold_strategy(m),
                                  forecast_error_pct=err, seed=seed).value
        fast = benchmark.tradeoff_curve(demand, margins=(m,), forecast_errors_pct=(err,), seeds=[seed])
        assert fast.mean_days_called.iloc[0] == slow.days_called.mean()
        assert fast.mean_capture_rate.iloc[0] == slow.capture_rate.mean()
