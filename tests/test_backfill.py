from datetime import date, datetime

from ewmodels.data import ieso_backfill as bf

from .test_forecast import _xml

LISTING = """<html><body><pre>
<a href="PUB_Adequacy3.xml">PUB_Adequacy3.xml</a>  22-Oct-2026 09:42  159K
<a href="PUB_Adequacy3_20260801_v120.xml">PUB_Adequacy3_20260801_v120.xml</a>  01-Aug-2026 23:49  190K
<a href="PUB_Adequacy3_20261010_v58.xml">PUB_Adequacy3_20261010_v58.xml</a>  05-Oct-2026 09:01  163K
<a href="PUB_Adequacy3_20261010_v64.xml">PUB_Adequacy3_20261010_v64.xml</a>  08-Oct-2026 15:28  163K
<a href="PUB_Adequacy3_20261010_v66.xml">PUB_Adequacy3_20261010_v66.xml</a>  09-Oct-2026 08:59  163K
<a href="PUB_Adequacy3_20261010_v67.xml">PUB_Adequacy3_20261010_v67.xml</a>  09-Oct-2026 15:30  163K
<a href="PUB_Adequacy3_20261010_v119.xml">PUB_Adequacy3_20261010_v119.xml</a>  09-Oct-2026 23:49  190K
<a href="PUB_Adequacy3_20261010_v120.xml">PUB_Adequacy3_20261010_v120.xml</a>  10-Oct-2026 23:49  190K
</pre></body></html>"""

CREATED = {"v58": "2026-10-05T09:01:00", "v64": "2026-10-08T15:28:00", "v66": "2026-10-09T08:59:00",
           "v67": "2026-10-09T15:30:00", "v119": "2026-10-09T23:49:00", "v120": "2026-10-10T23:49:00"}


def fake_fetch(url):
    if url == bf.LISTING_URL:
        return LISTING
    name = url.rsplit("/", 1)[-1]
    ymd, v = name[len("PUB_Adequacy3_"):-4].split("_")
    d = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
    created = CREATED.get(v, f"{d}T23:49:00")
    return _xml(delivery=d, created=created, base=20000 + int(v[1:]))


def test_parse_listing_skips_unversioned():
    lv = bf.parse_listing(LISTING)
    assert len(lv) == 7
    assert lv[0].delivery == date(2026, 8, 1) and lv[0].version == 120


def test_plan_picks_latest_before_each_cutoff():
    picks = {lv.version: label for lv, label in bf.plan(bf.parse_listing(LISTING))}
    assert picks[64] == "d2_1600"
    assert picks[66] == "d1_1000"
    assert picks[67] == "d1_1600"
    assert picks[119] == "d0_0000"
    assert 120 not in picks and 58 not in picks  # final-day version is never a forecast


def test_backfill_idempotent_and_rejects_same_day(tmp_path):
    p = tmp_path / "a.csv"
    s1 = bf.backfill(p, fetch=fake_fetch, now_utc=datetime(2026, 10, 22))
    assert s1["downloaded"] == 4 and s1["rows_appended"] == 4 * 24
    s2 = bf.backfill(p, fetch=fake_fetch, now_utc=datetime(2026, 10, 22))
    assert s2["downloaded"] == 0 and s2["rows_appended"] == 0
    assert bf.coverage_gaps(p, date(2026, 10, 9), date(2026, 10, 10)) == [date(2026, 10, 9)]


def test_backfill_feeds_load_archive(tmp_path):
    from ewmodels.data.ieso_forecast import load_archive
    p = tmp_path / "a.csv"
    bf.backfill(p, fetch=fake_fetch)
    day_ahead = load_archive(p, lead_hours=8)  # latest created <= D-1 16:00
    assert day_ahead.iloc[0] == 20000 + 67 + 2400
