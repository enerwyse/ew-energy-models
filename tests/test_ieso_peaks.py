import io
from datetime import datetime

import pandas as pd
import pytest

openpyxl = pytest.importorskip("openpyxl")

from ewmodels.data.ieso import EST
from ewmodels.data import ieso_peaks
from ewmodels.ontario import cost, ici

from .conftest import PLANTED
from .test_cost import flat_site, ga_frame


def workbook(official_2023=PLANTED):
    wb = openpyxl.Workbook()
    old = wb.active
    old.title = "May 1, 2017 to April 30, 2018"
    old.append(["Top 5 Peaks: Hours & System-Wide Consumption (Base Period: May 1, 2017 to April 30, 2018)"])
    old.append(["Rank", "Date", "Hour Ending (EST)", "Allocated Quantity of Energy Withdrawn (MWh)",
                "Embedded Generation (MWh)", "Total (MWh)*"])
    old.append([1, "September\xa025, 2017", 17, 21170.5, 641.1, 21811.6])
    for i, d in enumerate(["2017-09-26", "2017-06-12", "2018-01-05", "2017-07-19"], start=2):
        old.append([i, datetime.fromisoformat(d), 18, 20000.0, 500.0, 20500.0])
    old.append(["\xa0*The value in the Total (MWh) column is the number used ..."])
    new = wb.create_sheet("May 1, 2023 to April 30, 2024")
    new.append(["Top 5 Peaks: Hours & System-Wide Consumption  (Base Period: May 1, 2023 to April 30, 2024)"])
    new.append(["Rank", "Date", "Hour Ending (EST)", "Allocated Quantity of Energy Withdrawn (MWh)",
                "Embedded Generation (MWh)", "Energy Storage Injections (MWh)", "Total (MWh)", None])
    for i, (ts, mw) in enumerate(official_2023, start=1):
        t = pd.Timestamp(ts)
        new.append([i, datetime(t.year, t.month, t.day), t.hour + 1, mw - 1000, 900.0, 100.0, mw, None])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_parse_both_formats():
    df = ieso_peaks.parse_top5_peaks(workbook())
    assert list(df.base_period.unique()) == ["2017-2018", "2023-2024"]
    r1 = df.iloc[0]
    assert r1.interval_start == pd.Timestamp("2017-09-25 16:00", tz=EST) and r1.total_mwh == 21811.6
    assert r1.storage_mwh == 0.0
    new = df[df.base_period == "2023-2024"]
    assert new.interval_start.tolist() == [pd.Timestamp(t, tz=EST) for t, _ in PLANTED]


def test_reproduced_peaks_match_official(demand):
    off = ieso_peaks.parse_top5_peaks(workbook())
    cmp = ieso_peaks.compare_with_official(ici.top_peak_hours(demand).value, off).set_index("base_period")
    assert cmp.loc["2023-2024", "matched"] == 5
    plain = ici.top_peak_hours(demand, one_per_day=False).value
    cmp_plain = ieso_peaks.compare_with_official(plain, off).set_index("base_period")
    assert cmp_plain.loc["2023-2024", "matched"] == 4  # the same-day distractor displaces one
    assert cmp_plain.loc["2023-2024", "extra_in_ours"] == ["2023-07-05 HE18"]


def test_cost_uses_official_denominator(demand):
    shifted = [(t, mw + 1000) for t, mw in PLANTED]  # official Total differs from Ontario Demand
    off = ieso_peaks.parse_top5_peaks(workbook(shifted))
    t = cost.peak_hour_value(demand, ga_frame(), official=off).value.set_index("base_period")
    assert t.loc["2023-2024", "peaks_source"] == "IESO official"
    assert t.loc["2023-2024", "system_peak_mwh"] == pytest.approx(sum(mw for _, mw in shifted))
    assert t.loc["2024-2025", "peaks_source"] == "reproduced"
    r = cost.site_pdf(flat_site(), demand, official=off)
    assert r.value["2023-2024"] == pytest.approx(5.0 / sum(mw for _, mw in shifted))
