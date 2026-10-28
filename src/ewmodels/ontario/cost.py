"""Ontario Class A cost attribution (catalogue Model 3, re-scoped).

The catalogue's Model 3 priced consumption on time-of-use rates. Class A customers
don't pay TOU. Their commodity cost has two parts:

    energy  = Σ_h kWh(h) × OEMP(h) / 1000            OEMP = DA zonal price + LFDA, $/MWh
    GA      = Σ_m PDF(base period of m) × GA_total(m)  Class A
            = Σ_m kWh(m) × GA_rate(m) / 1000           Class B, for comparison

PDF is the site's share of Ontario withdrawals in the five peak hours of the base
period (May–Apr) that sets the adjustment period (Jul–Jun) containing month m.

So the GA a Class A site pays all year was decided by its load in five hours of
the previous base period. `peak_hour_value()` turns that into a price: the GA
dollars that one MWh consumed in a peak hour cost over the adjustment period.

Delivery, regulatory charges, the Ontario Electricity Rebate and taxes are not
included unless you pass your own delivery rates: they are utility-specific.
This is an analysis tool, not a settlement engine.
"""

from __future__ import annotations

import pandas as pd

from ewmodels.data.ieso import EST
from ewmodels.ontario import ici
from ewmodels.receipts import Check, Result, make_receipt
from ewmodels.resolution import to_hourly_energy

_DENOM_CAVEAT = ("PDF denominator approximated by IESO Ontario Demand in the peak hours; IESO "
                 "settles on total allocated withdrawals, so PDFs and $/MWh-at-peak are estimates.")
_GA_CAVEAT = ("GA total ($) is the IESO monthly Global Adjustment for all customers; Class A "
              "charge = PDF × this total. GA can be negative (a credit), as in Dec 2025–Feb 2026.")


def adjustment_base_period(month: str) -> str:
    """Base period whose peaks set Class A GA in `month` ('YYYY-MM'): Jul Y–Jun Y+1 <- May Y-1–Apr Y."""
    y, m = int(month[:4]), int(month[5:7])
    start = y if m >= 7 else y - 1
    return f"{start - 1}-{start}"


def _ga_month(ga: pd.DataFrame, month: str) -> tuple[float, float, str]:
    """(total $, rate $/MWh, basis) for a month, preferring actuals over estimates."""
    if month not in ga.index:
        return float("nan"), float("nan"), "missing"
    r = ga.loc[month]
    if pd.notna(r.get("actual")) and pd.notna(r.get("actual_rate")):
        return float(r["actual"]), float(r["actual_rate"]), "actual"
    if pd.notna(r.get("second_estimate")) and pd.notna(r.get("second_estimate_rate")):
        return float(r["second_estimate"]), float(r["second_estimate_rate"]), "second_estimate"
    return float("nan"), float("nan"), "missing"


def peak_hour_value(ontario_demand_mw: pd.Series, ga: pd.DataFrame) -> Result[pd.DataFrame]:
    """GA dollars per MWh consumed in a base period's peak hours, over its adjustment period.

    For each base period with peaks: $/MWh_at_peak = Σ_adjustment months GA_total / Σ_peaks Ontario MWh.
    Only months with an actual or second-estimate GA are summed; `months_with_ga` says how many of 12.
    """
    peaks = ici.top_peak_hours(ontario_demand_mw)
    rows = []
    for bp, grp in peaks.value.groupby("base_period"):
        y = int(bp.split("-")[1])
        months = [f"{yy}-{mm:02d}" for yy, mm in [(y, m) for m in range(7, 13)] + [(y + 1, m) for m in range(1, 7)]]
        vals = [_ga_month(ga, m) for m in months]
        used = [(m, v) for m, v in zip(months, vals) if v[2] != "missing"]
        ga_sum = sum(v[0] for _, v in used)
        sys_mwh = float(grp["demand_mw"].sum())  # 1-h intervals: MW == MWh
        rows.append({"base_period": bp, "peaks_complete": bool(grp["complete"].all()),
                     "adjustment_period": f"{y}-07 .. {y + 1}-06", "months_with_ga": len(used),
                     "ga_total_dollars": ga_sum, "system_peak_mwh": sys_mwh,
                     "dollars_per_mwh_at_peak": ga_sum / sys_mwh if used else float("nan"),
                     "estimated_months": sum(1 for _, v in used if v[2] != "actual")})
    table = pd.DataFrame(rows)
    checks = list(peaks.receipt.checks) + [
        Check("ga_available", bool((table["months_with_ga"] > 0).any()),
              f"GA months per base period: {dict(zip(table.base_period, table.months_with_ga))}")]
    caveats = list(peaks.receipt.caveats) + [_DENOM_CAVEAT, _GA_CAVEAT,
        "Partial adjustment periods (months_with_ga < 12) understate the full-year value."]
    receipt = make_receipt("ontario.cost.peak_hour_value", ontario_demand_mw, checks=checks, caveats=caveats)
    return Result(table, receipt)


def site_pdf(site_kwh: pd.Series, ontario_demand_mw: pd.Series) -> Result[dict]:
    """Estimated PDF per base period from a site's meter data and Ontario demand."""
    peaks = ici.top_peak_hours(ontario_demand_mw).value
    out, checks, caveats = {}, [], [_DENOM_CAVEAT]
    for bp, grp in peaks.groupby("base_period"):
        hours = pd.DatetimeIndex(grp["interval_start"])
        sysm = pd.Series(grp["demand_mw"].to_numpy(), index=hours)
        r = ici.peak_demand_factor(site_kwh, hours, sysm, denominator_source="ontario_demand")
        if r.receipt.valid:
            out[bp] = r.value
        checks.append(Check(f"pdf_{bp}", r.receipt.valid,
                            next((c.detail for c in r.receipt.checks if not c.passed), "all peak hours metered")))
        if not grp["complete"].all():
            caveats.append(f"Base period {bp} is still open: its peaks (and PDF) are provisional.")
    receipt = make_receipt("ontario.cost.site_pdf", site_kwh, checks=checks, caveats=caveats)
    receipt.parameters["pdf"] = {k: round(v, 8) for k, v in out.items()}
    return Result(out, receipt)


def bill_breakdown(
    site_kwh: pd.Series,
    prices: pd.DataFrame | pd.Series,
    ga: pd.DataFrame,
    pdf: dict[str, float] | float,
    *,
    delivery: dict | None = None,
) -> Result[pd.DataFrame]:
    """Monthly commodity cost of a site as Class A and as Class B.

    site_kwh: interval energy, tz-aware, hourly or finer.
    prices: hourly $/MWh indexed by interval start (EST): the frame from
        ieso_prices.oemp() (uses its 'oemp' and 'lfda_final' columns) or a plain Series.
    ga: monthly GA frame from ieso_prices.load_ga().
    pdf: {base_period: PDF} (from site_pdf) or one PDF for every month.
    delivery: optional {'kwh_rate': $/kWh, 'kw_month_rate': $/kW of monthly peak}. Utility-specific,
        so there are no defaults.

    Only months fully covered by both meter data and prices are billed.
    """
    kwh = to_hourly_energy(site_kwh)
    kwh.index = kwh.index.tz_convert(EST)
    if isinstance(prices, pd.Series):
        px = pd.DataFrame({"oemp": prices, "lfda_final": True})
    else:
        px = prices[["oemp", "lfda_final"]].copy()
    px.index = px.index.tz_convert(EST)
    df = pd.DataFrame({"kwh": kwh}).join(px, how="inner")
    df["energy_dollars"] = df["kwh"] * df["oemp"] / 1000.0
    df["month"] = df.index.strftime("%Y-%m")

    rows, skipped = [], []
    for month, g in df.groupby("month"):
        start = pd.Timestamp(f"{month}-01", tz=EST)
        hours = int((start + pd.offsets.MonthBegin(1) - start) / pd.Timedelta(hours=1))
        if g["kwh"].notna().sum() < hours or g["oemp"].notna().sum() < hours:
            skipped.append(month)
            continue
        total, rate, basis = _ga_month(ga, month)
        bp = adjustment_base_period(month)
        p = pdf if isinstance(pdf, (int, float)) else pdf.get(bp, float("nan"))
        row = {"month": month, "kwh": g["kwh"].sum(), "energy_dollars": g["energy_dollars"].sum(),
               "avg_oemp": g["energy_dollars"].sum() / g["kwh"].sum() * 1000.0 if g["kwh"].sum() else float("nan"),
               "base_period": bp, "pdf": p, "ga_basis": basis,
               "ga_class_a_dollars": p * total, "ga_class_b_dollars": g["kwh"].sum() * rate / 1000.0,
               "hours_lfda_not_final": int((~g["lfda_final"]).sum())}
        if delivery:
            row["delivery_dollars"] = (g["kwh"].sum() * delivery.get("kwh_rate", 0.0)
                                       + g["kwh"].max() * delivery.get("kw_month_rate", 0.0))
        rows.append(row)
    table = pd.DataFrame(rows)
    if len(table):
        extra = table["delivery_dollars"] if delivery else 0.0
        table["total_class_a"] = table["energy_dollars"] + table["ga_class_a_dollars"] + extra
        table["total_class_b"] = table["energy_dollars"] + table["ga_class_b_dollars"] + extra
        table["ga_share_class_a"] = table["ga_class_a_dollars"] / table["total_class_a"]

    checks = [
        Check("months_billed", len(table) > 0, f"{len(table)} full months; skipped (incomplete): {skipped or 'none'}"),
        Check("ga_available", bool(len(table)) and bool((table["ga_basis"] != "missing").all()),
              f"GA basis by month: {dict(zip(table.month, table.ga_basis)) if len(table) else {}}"),
        Check("pdf_available", bool(len(table)) and bool(table["pdf"].notna().all()),
              "PDF needed for each billed month's base period"),
        Check("no_negative_energy", bool((kwh.dropna() >= 0).all()), "net-metered sites need separate treatment"),
    ]
    caveats = [_GA_CAVEAT,
               "Commodity only (energy + GA)" + (" + your delivery rates" if delivery else "")
               + ": regulatory charges, the Ontario Electricity Rebate and taxes are excluded.",
               "Energy priced at OEMP for both classes; LFDA treated as 0 where not yet final "
               "(see hours_lfda_not_final)."]
    if isinstance(pdf, dict):
        caveats.append(_DENOM_CAVEAT)
    receipt = make_receipt("ontario.cost.bill_breakdown", site_kwh,
                           parameters={"delivery": delivery, "pdf": pdf}, checks=checks, caveats=caveats)
    return Result(table, receipt)
