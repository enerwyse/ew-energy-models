"""Industrial Conservation Initiative (ICI) arithmetic — catalogue Models 9/10.

Class A customers pay Global Adjustment (GA) in proportion to their Peak
Demand Factor (PDF): their share of electricity withdrawn during the five
highest Ontario-demand hours of a base period (May 1 – April 30). The PDF then
sets their GA share for the following adjustment period (July 1 – June 30).

    PDF        = Σ_h∈peaks customer_MWh(h) / Σ_h∈peaks system_MWh(h)
    GA_month   = PDF × total_GA_month ($)

Simplifications are recorded as caveats in every receipt. This module is an
analysis tool, not a settlement engine: your LDC / IESO invoice is authoritative.
"""

from __future__ import annotations

import pandas as pd

from ewmodels.data.ieso import EST
from ewmodels.receipts import Check, Result, make_receipt
from ewmodels.resolution import detect_resolution, to_hourly_energy

N_PEAKS = 5
_PEAKS_CAVEAT = (
    "Peak hours reproduced from public IESO Ontario Demand data; the official "
    "peak hours are those IESO publishes after each base period closes."
)


def base_period_label(ts: pd.Timestamp) -> str:
    """'2025-2026' for any timestamp in May 1 2025 – Apr 30 2026 (evaluated in EST)."""
    t = ts.tz_convert(EST) if ts.tzinfo else ts
    y = t.year if t.month >= 5 else t.year - 1
    return f"{y}-{y + 1}"


def base_period_bounds(label: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[start, end) of a base period in EST."""
    y = int(label.split("-")[0])
    return (pd.Timestamp(f"{y}-05-01", tz=EST), pd.Timestamp(f"{y + 1}-05-01", tz=EST))


def top_peak_hours(ontario_demand_mw: pd.Series, n: int = N_PEAKS) -> Result[pd.DataFrame]:
    """Top-n Ontario-demand hours per base period.

    Returns a frame with columns base_period, rank, interval_start, demand_mw,
    complete (whether the base period has full hourly coverage — an incomplete
    period's peaks are provisional).
    """
    s = ontario_demand_mw.dropna().sort_index()
    if s.index.tz is None:
        raise ValueError("demand index must be timezone-aware (IESO data is EST)")
    s = s.tz_convert(EST)
    labels = s.index.map(base_period_label)
    rows = []
    coverage = {}
    for label, grp in s.groupby(labels):
        start, end = base_period_bounds(label)
        expected = int((end - start) / pd.Timedelta(hours=1))
        coverage[label] = len(grp) / expected
        top = grp.nlargest(n)
        for rank, (ts, mw) in enumerate(top.items(), start=1):
            rows.append(
                {"base_period": label, "rank": rank, "interval_start": ts, "demand_mw": mw,
                 "complete": coverage[label] >= 0.999}
            )
    table = pd.DataFrame(rows)
    checks = [
        Check("hourly_resolution", detect_resolution(s.index) == pd.Timedelta(hours=1),
              f"detected {detect_resolution(s.index)}"),
        Check("no_duplicate_hours", not s.index.duplicated().any(), "duplicate timestamps would double-count"),
    ]
    caveats = [_PEAKS_CAVEAT]
    partial = [k for k, v in coverage.items() if v < 0.999]
    if partial:
        caveats.append(f"Incomplete base periods (provisional peaks): {', '.join(partial)}")
    receipt = make_receipt("ontario.ici.top_peak_hours", s, parameters={"n": n},
                           checks=checks, caveats=caveats)
    receipt.parameters["coverage"] = {k: round(v, 4) for k, v in coverage.items()}
    return Result(table, receipt)


def _align(series_or_scalar, hours: pd.DatetimeIndex, name: str) -> pd.Series:
    if isinstance(series_or_scalar, pd.Series):
        s = series_or_scalar.copy()
        s.index = s.index.tz_convert("UTC")
        out = s.reindex(hours)
        if out.isna().any():
            raise ValueError(f"{name} is missing values for some peak hours")
        return out
    return pd.Series(float(series_or_scalar), index=hours)


def peak_demand_factor(
    customer_kwh: pd.Series,
    peak_hours: pd.DatetimeIndex,
    system_mwh: pd.Series,
    *,
    ppa_offset_kwh: pd.Series | float | None = None,
    denominator_source: str = "unspecified",
) -> Result[float]:
    """Peak Demand Factor from interval meter energy.

    customer_kwh: energy per interval (kWh), tz-aware index at hourly or finer
        resolution, indexed by interval start. Finer data is summed to hours.
    peak_hours: interval starts of the base period's peak hours (tz-aware).
    system_mwh: system withdrawal (MWh) for each peak hour. Use the IESO-published
        peak-hour totals for billing-grade work (denominator_source="ieso_published");
        Ontario Demand is an approximation.
    ppa_offset_kwh: optional clean-energy PPA generation credited in each peak
        hour (per-hour Series or a flat kWh per hour). Offsets floor at zero.
    """
    hours = pd.DatetimeIndex(peak_hours).tz_convert("UTC")
    hourly = to_hourly_energy(customer_kwh)
    hourly.index = hourly.index.tz_convert("UTC")
    cust = hourly.reindex(hours)
    present = cust.notna()
    checks = [
        Check("all_peak_hours_metered", bool(present.all()),
              f"{int(present.sum())}/{len(hours)} peak hours present in meter data"),
        Check("no_negative_energy", bool((hourly.dropna() >= 0).all()),
              "net-metered/exporting sites need separate treatment"),
    ]
    caveats = []
    if denominator_source != "ieso_published":
        caveats.append(f"Denominator source '{denominator_source}' is not the IESO-published "
                       "peak-hour withdrawal total; PDF is an estimate.")
    if ppa_offset_kwh is not None:
        cust = cust - _align(ppa_offset_kwh, hours, "ppa_offset_kwh")
        cust = cust.clip(lower=0)
        caveats.append("PPA offset applied as simple hourly netting; eligibility (registered "
                       "Class A participant, qualifying generator, filing deadlines) not checked.")
    sys = _align(system_mwh, hours, "system_mwh")
    pdf = float(cust.sum() / 1000.0 / sys.sum()) if present.all() else float("nan")
    receipt = make_receipt(
        "ontario.ici.peak_demand_factor", customer_kwh,
        parameters={"peak_hours": [str(h) for h in hours], "denominator_source": denominator_source,
                    "customer_peak_kwh": [round(float(v), 3) for v in cust.fillna(float("nan"))]},
        checks=checks, caveats=caveats,
    )
    return Result(pdf, receipt)


def ga_charge(pdf: float, total_ga_dollars: float) -> float:
    """Class A GA charge for a month: PDF × total Ontario GA cost for that month."""
    return pdf * total_ga_dollars


def simulate_curtailment(
    customer_kwh: pd.Series,
    peak_hours: pd.DatetimeIndex,
    system_mwh: pd.Series,
    curtail_kw: float,
    adjustment_period_ga_dollars: float,
    *,
    hit_hours: pd.DatetimeIndex | None = None,
    denominator_source: str = "unspecified",
) -> Result[dict]:
    """GA dollars saved over an adjustment period by curtailing `curtail_kw`
    during the peak hours actually hit (default: all of them — perfect foresight).

    The curtailed energy is also removed from the system denominator.
    """
    hours = pd.DatetimeIndex(peak_hours).tz_convert("UTC")
    hit = hours if hit_hours is None else pd.DatetimeIndex(hit_hours).tz_convert("UTC").intersection(hours)
    base = peak_demand_factor(customer_kwh, hours, system_mwh, denominator_source=denominator_source)

    hourly = to_hourly_energy(customer_kwh)
    hourly.index = hourly.index.tz_convert("UTC")
    reduced = hourly.copy()
    hit = hit.intersection(reduced.index)
    cut = (reduced.reindex(hit).clip(upper=curtail_kw)).fillna(0)  # kWh in a 1-h interval
    reduced.loc[hit] = reduced.loc[hit] - cut
    sys = _align(system_mwh, hours, "system_mwh").copy()
    sys.loc[hit] = sys.loc[hit] - cut / 1000.0
    scen = peak_demand_factor(reduced, hours, sys, denominator_source=denominator_source)

    value = {
        "baseline_pdf": base.value,
        "scenario_pdf": scen.value,
        "peak_hours_hit": int(len(hit)),
        "baseline_ga_dollars": ga_charge(base.value, adjustment_period_ga_dollars),
        "scenario_ga_dollars": ga_charge(scen.value, adjustment_period_ga_dollars),
    }
    value["savings_dollars"] = value["baseline_ga_dollars"] - value["scenario_ga_dollars"]
    receipt = base.receipt
    receipt.model = "ontario.ici.simulate_curtailment"
    receipt.parameters.update({"curtail_kw": curtail_kw, "hit_hours": [str(h) for h in hit],
                               "adjustment_period_ga_dollars": adjustment_period_ga_dollars})
    receipt.caveats.append("Assumes curtailment is a flat kW reduction held for each hit hour, "
                           "and that the GA pool is unaffected by the curtailment.")
    return Result(value, receipt)
