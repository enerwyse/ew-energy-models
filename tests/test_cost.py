import pandas as pd
import pytest

from ewmodels.data.ieso import EST
from ewmodels.ontario import cost, ici

from .conftest import PLANTED


def months(a, b):
    return [p.strftime("%Y-%m") for p in pd.period_range(a, b, freq="M")]


def ga_frame(total=100e6, rate=50.0, first="2024-07", last="2026-06", negative=()):
    ms = months(first, last)
    t = [(-total if m in negative else total) for m in ms]
    r = [(-rate if m in negative else rate) for m in ms]
    return pd.DataFrame({"actual": t, "actual_rate": r, "second_estimate": t, "second_estimate_rate": r},
                        index=pd.Index(ms, name="trade_month"))


def flat_site(kw=1000.0, start="2023-05-01", end="2025-08-01", zero_at=()):
    idx = pd.date_range(start, end, freq="1h", inclusive="left", tz=EST)
    s = pd.Series(kw, index=idx.tz_convert("UTC"))
    for ts in zero_at:
        s[pd.Timestamp(ts).tz_convert("UTC")] = 0.0
    return s


def prices(start="2024-07-01", end="2025-08-01", px=50.0):
    idx = pd.date_range(start, end, freq="1h", inclusive="left", tz=EST)
    return pd.Series(px, index=idx)


def test_adjustment_base_period():
    assert cost.adjustment_base_period("2026-08") == "2025-2026"
    assert cost.adjustment_base_period("2026-06") == "2024-2025"
    assert cost.adjustment_base_period("2026-03") == "2024-2025"


def test_peak_hour_value(demand):
    t = cost.peak_hour_value(demand, ga_frame()).value.set_index("base_period")
    sys_mwh = sum(mw for _, mw in PLANTED)
    row = t.loc["2023-2024"]
    assert row.months_with_ga == 12
    assert row.dollars_per_mwh_at_peak == pytest.approx(12 * 100e6 / sys_mwh)
    assert t.loc["2024-2025", "months_with_ga"] == 12  # Jul 2025 .. Jun 2026


def test_site_pdf_flat(demand):
    r = cost.site_pdf(flat_site(), demand)
    sys_mwh = sum(mw for _, mw in PLANTED)
    assert r.value["2023-2024"] == pytest.approx(5.0 / sys_mwh)
    assert r.receipt.valid


def test_bill_breakdown_class_a_vs_b(demand):
    site = flat_site()
    pdf = cost.site_pdf(site, demand).value
    r = cost.bill_breakdown(site, prices(), ga_frame(), pdf)
    t = r.value.set_index("month")
    assert r.receipt.valid
    assert list(t.index) == months("2024-07", "2025-07")
    jan = t.loc["2025-01"]
    assert jan.kwh == pytest.approx(744 * 1000)
    assert jan.energy_dollars == pytest.approx(744 * 1000 * 50 / 1000)
    assert jan.base_period == "2023-2024"
    assert jan.ga_class_a_dollars == pytest.approx(pdf["2023-2024"] * 100e6)
    assert jan.ga_class_b_dollars == pytest.approx(744 * 50.0)
    assert t.loc["2025-07", "base_period"] == "2024-2025"


def test_curtailing_at_peaks_removes_class_a_ga(demand):
    peaks = [ts for ts, _ in PLANTED]
    site = flat_site(zero_at=[pd.Timestamp(p, tz=EST) for p in peaks])
    pdf = cost.site_pdf(site, demand).value
    t = cost.bill_breakdown(site, prices(end="2025-07-01"), ga_frame(), pdf).value
    assert pdf["2023-2024"] == 0.0
    assert (t.ga_class_a_dollars == 0).all() and (t.ga_class_b_dollars > 0).all()


def test_negative_ga_is_a_credit(demand):
    site = flat_site()
    pdf = cost.site_pdf(site, demand).value
    t = cost.bill_breakdown(site, prices(), ga_frame(negative=("2025-01",)), pdf).value.set_index("month")
    assert t.loc["2025-01", "ga_class_a_dollars"] < 0
    assert t.loc["2025-01", "ga_class_b_dollars"] < 0


def test_incomplete_month_skipped_and_delivery(demand):
    site = flat_site()
    px = prices(start="2024-07-15", end="2024-09-01")
    r = cost.bill_breakdown(site, px, ga_frame(), 0.001, delivery={"kwh_rate": 0.01, "kw_month_rate": 5.0})
    t = r.value
    assert list(t.month) == ["2024-08"]
    assert "2024-07" in next(c.detail for c in r.receipt.checks if c.name == "months_billed")
    assert t.delivery_dollars.iloc[0] == pytest.approx(744 * 1000 * 0.01 + 1000 * 5.0)
    assert t.total_class_a.iloc[0] == pytest.approx(
        t.energy_dollars.iloc[0] + 0.001 * 100e6 + t.delivery_dollars.iloc[0])


def test_missing_ga_fails_check(demand):
    site = flat_site()
    pdf = cost.site_pdf(site, demand).value
    r = cost.bill_breakdown(site, prices(), ga_frame(first="2024-08"), pdf)
    assert not r.receipt.valid
