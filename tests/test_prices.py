import pandas as pd
import pytest

from ewmodels.data import ieso_prices as ip

NS = 'xmlns="http://www.ieso.ca/schema"'


def da_xml(day, rev=1, created="2026-10-26T12:31:15", base=40.0):
    hours = "".join(
        f"<HourlyPriceComponents><PricingHour>{h}</PricingHour><ZonalPrice>{base + h}</ZonalPrice>"
        f"<LossPriceCapped>0.37</LossPriceCapped><CongestionPriceCapped>0.8</CongestionPriceCapped>"
        f"<Flag>DSO-RD</Flag></HourlyPriceComponents>" for h in range(1, 25))
    return (f'<?xml version="1.0" encoding="UTF-8"?><Document docID="DAHourlyOntarioZonalPrice" {NS}>'
            f"<DocHeader><DocTitle>Day Ahead Hourly Ontario Zonal Energy Price Report</DocTitle>"
            f"<DocRevision>{rev}</DocRevision><CreatedAt>{created}</CreatedAt></DocHeader>"
            f"<DocBody><DeliveryDate>{day}</DeliveryDate>{hours}</DocBody></Document>")


def ga_xml(month, rev=1, created="2026-10-15T14:30:10", actual="611512762", rate="64.77"):
    return (f'<?xml version="1.0"?><Document {NS}><DocHeader><DocRevision>{rev}</DocRevision>'
            f"<CreatedAt>{created}</CreatedAt></DocHeader><DocBody><TradeMonth>{month}</TradeMonth>"
            f"<GAValues><FirstEstimateRate>64.78</FirstEstimateRate><SecondEstimate>583245920</SecondEstimate>"
            f"<SecondEstimateRate>63.51</SecondEstimateRate><Actual>{actual}</Actual>"
            f"<ActualRate>{rate}</ActualRate></GAValues></DocBody></Document>")


def listing(names):
    return "".join(f'<a href="{n}">{n}</a> 29-Jul-2026 12:33 6.0K\n' for n in names)


def test_parse_da():
    p = ip.parse_da_ozp(da_xml("2026-10-27"))
    assert p.delivery_date.isoformat() == "2026-10-27" and p.revision == 1
    assert len(p.rows) == 24 and p.rows[0] == {"hour_ending": 1, "zonal_price": 41.0, "loss_price": 0.37,
                                                "congestion_price": 0.8, "flag": "DSO-RD"}


def test_parse_ga():
    g = ip.parse_ga(ga_xml("2026-09"))
    assert g["trade_month"] == "2026-09" and g["actual"] == 611512762 and g["actual_rate"] == 64.77


def test_parse_ga_blank_actual():
    g = ip.parse_ga(ga_xml("2026-10", actual="", rate=""))
    assert pd.isna(g["actual"]) and g["first_estimate_rate"] == 64.78


def test_parse_lfda():
    text = "---\nDate,Hour,Status,LFDA Rate ($/MWh)\n2025-05-01,1,Final,\n2025-07-23,12,Final,0.2\n"
    df = ip.parse_lfda_csv(text)
    assert df.index[0] == pd.Timestamp("2025-05-01 00:00", tz=ip.EST)
    assert pd.isna(df.lfda.iloc[0]) and df.lfda.iloc[1] == 0.2


def make_fetch(files):
    calls = []

    def fetch(url):
        calls.append(url)
        name = url.rsplit("/", 1)[-1]
        if name == "":
            folder = url.rstrip("/").rsplit("/", 1)[-1]
            return listing(n for n in files if folder in n)
        return files[name]
    return fetch, calls


def test_da_archive_idempotent_and_revisions(tmp_path):
    arc = tmp_path / "da.csv"
    files = {
        "PUB_DAHourlyOntarioZonalPrice.xml": da_xml("2026-10-28"),
        "PUB_DAHourlyOntarioZonalPrice_20261027.xml": da_xml("2026-10-27"),
        "PUB_DAHourlyOntarioZonalPrice_20261027_v1.xml": da_xml("2026-10-27"),
        "PUB_DAHourlyOntarioZonalPrice_20261028.xml": da_xml("2026-10-28"),
        "PUB_DAHourlyOntarioZonalPrice_20261028_v1.xml": da_xml("2026-10-28"),
    }
    fetch, calls = make_fetch(files)
    r = ip.archive_da_prices(arc, fetch=fetch)
    assert r["files_fetched"] == 2 and r["rows_appended"] == 48
    r = ip.archive_da_prices(arc, fetch=fetch)
    assert r["files_fetched"] == 0
    # IESO republishes 10-27 with administered prices
    files["PUB_DAHourlyOntarioZonalPrice_20261027_v2.xml"] = da_xml("2026-10-27", rev=2,
                                                                     created="2026-11-03T09:00:00", base=100)
    fetch, _ = make_fetch(files)
    r = ip.archive_da_prices(arc, fetch=fetch)
    assert r["files_fetched"] == 1
    s = ip.load_da_prices(arc)
    assert len(s) == 48
    assert s[pd.Timestamp("2026-10-27 00:00", tz=ip.EST)] == 101.0  # latest version wins
    assert s[pd.Timestamp("2026-10-28 23:00", tz=ip.EST)] == 64.0
    assert ip.da_price_gaps(arc, pd.Timestamp("2026-10-26").date(), pd.Timestamp("2026-10-28").date()) == \
        [pd.Timestamp("2026-10-26").date()]


def test_da_archive_rejects_mismatched_date(tmp_path):
    files = {"PUB_DAHourlyOntarioZonalPrice_20261027_v1.xml": da_xml("2026-10-26")}
    fetch, _ = make_fetch(files)
    with pytest.raises(ValueError):
        ip.archive_da_prices(tmp_path / "da.csv", fetch=fetch)


def test_ga_archive_keeps_all_versions(tmp_path):
    arc = tmp_path / "ga.csv"
    files = {
        "PUB_GlobalAdjustment.xml": ga_xml("2026-10"),
        "PUB_GlobalAdjustment_202609.xml": ga_xml("2026-09"),
        "PUB_GlobalAdjustment_202609_v2.xml": ga_xml("2026-09", actual="", rate="", created="2026-09-30T10:00:00"),
        "PUB_GlobalAdjustment_202609_v3.xml": ga_xml("2026-09"),
        "PUB_GlobalAdjustment_202610_v1.xml": ga_xml("2026-10", actual="", rate="", created="2026-10-01T10:00:00"),
    }
    fetch, _ = make_fetch(files)
    assert ip.archive_ga(arc, fetch=fetch)["rows_appended"] == 3
    assert ip.archive_ga(arc, fetch=fetch)["rows_appended"] == 0
    ga = ip.load_ga(arc)
    assert ga.loc["2026-09", "actual"] == 611512762 and ga.loc["2026-09", "version"] == 3
    assert pd.isna(ga.loc["2026-10", "actual"])


def test_oemp_adds_lfda_and_flags_missing():
    idx = pd.date_range("2026-07-01", periods=3, freq="1h", tz=ip.EST)
    da = pd.Series([30.0, 40.0, 50.0], index=idx)
    lf = pd.DataFrame({"lfda": [1.0, None], "status": ["Final", "Final"]}, index=idx[:2])
    o = ip.oemp(da, lf)
    assert o.oemp.tolist() == [31.0, 40.0, 50.0]
    assert o.lfda_final.tolist() == [True, False, False]
