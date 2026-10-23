"""Facility load analytics: catalogue Models 1, 2 and 5.

All three take interval *energy* (kWh per interval) with a timezone-aware
index at any resolution (5, 15, 60 min ...). Resolution is detected, never
assumed, and demand is derived as  kW = kWh / interval_hours.

    load_profile(energy)     Model 1: baseload, typical and high load; hour x day-type profile
    rank_peaks(energy)       Model 2: top-N peaks, when they cluster, and coincidence with
                             system peaks (e.g. Ontario ICI hours from ewmodels.ontario.ici)
    load_factor(energy)      Model 5: monthly load factor with completeness checks

Every function returns a Result whose receipt records the input hash, detected
resolution, data checks and caveats.
"""

from __future__ import annotations

import pandas as pd

from ewmodels.receipts import Check, Result, make_receipt
from ewmodels.resolution import detect_resolution, to_hourly_energy

DEFAULT_TZ = "America/Toronto"
MIN_COVERAGE = 0.90


# ----------------------------------------------------------------- shared prep

def _prepare(energy_kwh: pd.Series, tz: str) -> tuple[pd.Series, pd.Series, pd.Timedelta, list[Check]]:
    """Validate interval energy and derive demand (kW) in site-local time."""
    if not isinstance(energy_kwh.index, pd.DatetimeIndex) or energy_kwh.index.tz is None:
        raise ValueError("energy series needs a timezone-aware DatetimeIndex (interval start)")
    raw = energy_kwh.sort_index()
    dupes = int(raw.index.duplicated().sum())
    e = raw[~raw.index.duplicated(keep="first")].dropna().astype(float)
    if len(e) < 2:
        raise ValueError("need at least two non-missing intervals")
    res = detect_resolution(e.index)
    hours = res / pd.Timedelta(hours=1)
    diffs = pd.Series(e.index).diff().dropna()
    regular = float((diffs == res).mean())
    expected = int((e.index.max() - e.index.min()) / res) + 1
    coverage = len(e) / expected
    e = e.tz_convert(tz)
    kw = (e / hours).rename("demand_kw")
    p99 = float(kw.quantile(0.99))
    spike_ratio = float(kw.max() / p99) if p99 > 0 else float("nan")
    p90 = float(kw.quantile(0.90))
    zero_share = float((kw <= 0.01 * p90).mean()) if p90 > 0 else 1.0
    checks = [
        Check("no_duplicate_timestamps", dupes == 0, f"{dupes} duplicate interval(s) dropped"),
        Check("no_negative_energy", bool((e >= 0).all()),
              "negative intervals suggest net metering/export; analyse import and export separately"),
        Check("coverage", coverage >= MIN_COVERAGE, f"{coverage:.1%} of expected intervals present"),
        Check("regular_spacing", regular >= 0.99, f"{regular:.1%} of gaps equal the detected {res}"),
        Check("few_zero_readings", zero_share <= 0.05,
              f"{zero_share:.1%} of intervals read zero or near-zero (<=1% of the P90 load); "
              "long runs like this usually mean meter outages, not a site that switched off"),
        Check("no_extreme_spikes", not spike_ratio > 2.0,
              f"max demand is {spike_ratio:.2f}x the 99th percentile; above 2x usually means a meter or data error"),
    ]
    return e, kw, res, checks


# ----------------------------------------------------------------- Model 1

def load_profile(energy_kwh: pd.Series, tz: str = DEFAULT_TZ) -> Result[dict]:
    """Where the energy goes: baseload, typical and high load, and the daily shape.

    value = {
      "summary": dict of baseload_kw (P10), typical_kw (P50), high_load_kw (P90),
                 max_kw, mean_kw, base_to_peak (P10/P90), always_on_share,
                 total_kwh, days,
      "profile": DataFrame indexed by local hour (0-23), columns MultiIndex
                 (day_type in {weekday, weekend}) x (p10, p50, p90) in kW,
    }

    always_on_share = baseload_kw x hours / total_kwh: the fraction of energy that
    would be used even if the site did nothing but sit idle. A high value points to
    equipment that never switches off.
    """
    e, kw, res, checks = _prepare(energy_kwh, tz)
    hours_total = len(kw) * (res / pd.Timedelta(hours=1))
    q = kw.quantile([0.10, 0.50, 0.90])
    total_kwh = float(e.sum())
    summary = {
        "baseload_kw": float(q[0.10]),
        "typical_kw": float(q[0.50]),
        "high_load_kw": float(q[0.90]),
        "max_kw": float(kw.max()),
        "mean_kw": float(kw.mean()),
        "base_to_peak": float(q[0.10] / q[0.90]) if q[0.90] > 0 else float("nan"),
        "always_on_share": min(1.0, float(q[0.10]) * hours_total / total_kwh) if total_kwh > 0 else float("nan"),
        "total_kwh": total_kwh,
        "days": float(hours_total / 24),
    }
    day_type = pd.Series(kw.index.day_of_week >= 5, index=kw.index).map({False: "weekday", True: "weekend"})
    prof = (kw.groupby([day_type.rename("day_type"), kw.index.hour.rename("hour")])
              .quantile([0.10, 0.50, 0.90]).unstack())
    prof.columns = ["p10", "p50", "p90"]
    prof = prof.unstack("day_type").swaplevel(0, 1, axis=1).sort_index(axis=1)
    checks.append(Check("at_least_14_days", summary["days"] >= 14,
                        f"{summary['days']:.0f} days; short records give unstable percentiles"))
    receipt = make_receipt("load.load_profile", energy_kwh, parameters={"tz": tz}, checks=checks, caveats=[
        "Statutory holidays are counted as their weekday, not as weekend days.",
        f"Percentiles are of demand at the detected resolution ({res}); finer data shows higher peaks.",
    ])
    return Result({"summary": summary, "profile": prof}, receipt)


# ----------------------------------------------------------------- Model 2

def rank_peaks(
    energy_kwh: pd.Series,
    n: int = 10,
    tz: str = DEFAULT_TZ,
    *,
    one_per_day: bool = True,
    system_peak_hours: pd.DatetimeIndex | None = None,
) -> Result[dict]:
    """The site's highest-demand intervals, when they cluster, and (optionally)
    what the site drew during system peak hours.

    one_per_day: keep only each day's highest interval, so one hot afternoon
        doesn't fill the whole top-N.
    system_peak_hours: interval starts of system peaks, e.g. the Ontario ICI top-5
        hours from ewmodels.ontario.ici.top_peak_hours. Adds `coincidence`: the
        site's hourly demand in each of those hours and the coincidence factor
        (mean demand in system peak hours / the site's own maximum hourly demand).
        A factor near 1 means the site peaks when the grid peaks: high ICI exposure.
    """
    e, kw, res, checks = _prepare(energy_kwh, tz)
    if one_per_day:
        idx = kw.groupby(kw.index.normalize()).idxmax().to_numpy()
        pool = kw.loc[idx]
    else:
        pool = kw
    top = pool.nlargest(n)
    site_max = float(kw.max())
    table = pd.DataFrame({
        "rank": range(1, len(top) + 1),
        "interval_start": top.index,
        "demand_kw": top.to_numpy(),
        "pct_of_max": top.to_numpy() / site_max if site_max else float("nan"),
        "hour": top.index.hour,
        "weekday": top.index.day_name(),
        "month": top.index.month_name(),
    })
    hours = table["hour"]
    mode_hour = int(hours.mode().iloc[0]) if len(hours) else None
    clustering = {
        "most_common_hour": mode_hour,
        "share_within_1h_of_mode": float(((hours - mode_hour).abs() <= 1).mean()) if mode_hour is not None else None,
        "share_on_weekdays": float((top.index.day_of_week < 5).mean()) if len(top) else None,
        "months": table["month"].value_counts().to_dict(),
    }
    value = {"peaks": table, "clustering": clustering}
    caveats = [f"Peaks are at the detected resolution ({res}); billing demand may use a different interval."]

    if system_peak_hours is not None:
        sys_h = pd.DatetimeIndex(system_peak_hours).tz_convert("UTC")
        hourly = to_hourly_energy(e).tz_convert("UTC")  # kWh in a 1-hour interval == average kW
        drawn = hourly.reindex(sys_h)
        site_max_hourly = float(hourly.max())
        present = int(drawn.notna().sum())
        value["coincidence"] = {
            "hours": pd.DataFrame({"system_peak_start": sys_h.tz_convert(tz), "site_kw": drawn.to_numpy()}),
            "coincidence_factor": float(drawn.mean() / site_max_hourly) if present and site_max_hourly else float("nan"),
            "site_max_hourly_kw": site_max_hourly,
        }
        checks.append(Check("system_peaks_metered", present == len(sys_h),
                            f"{present}/{len(sys_h)} system peak hours present in meter data"))
        caveats.append("Coincidence uses hourly-average demand, matching how ICI peaks are measured.")

    receipt = make_receipt("load.rank_peaks", energy_kwh,
                           parameters={"n": n, "tz": tz, "one_per_day": one_per_day}, checks=checks, caveats=caveats)
    return Result(value, receipt)


# ----------------------------------------------------------------- Model 5

def load_factor(energy_kwh: pd.Series, tz: str = DEFAULT_TZ, freq: str = "MS") -> Result[pd.DataFrame]:
    """Load factor per period (default calendar month, site-local):

        LF = average demand / peak demand = kWh / (peak_kW x metered hours)

    A low LF means capacity sized for short peaks and idle the rest of the time;
    peak shaving raises it. Periods under 90% coverage are flagged incomplete.
    """
    e, kw, res, checks = _prepare(energy_kwh, tz)
    step_h = res / pd.Timedelta(hours=1)
    g = kw.groupby(pd.Grouper(freq=freq))
    out = pd.DataFrame({
        "kwh": e.groupby(pd.Grouper(freq=freq)).sum(),
        "peak_kw": g.max(),
        "metered_hours": g.count() * step_h,
    })
    starts = out.index
    ends = [s + pd.tseries.frequencies.to_offset(freq) for s in starts]
    calendar_hours = [(b - a) / pd.Timedelta(hours=1) for a, b in zip(starts, ends)]
    out["coverage"] = out["metered_hours"] / calendar_hours
    out["avg_kw"] = out["kwh"] / out["metered_hours"]
    out["load_factor"] = out["avg_kw"] / out["peak_kw"]
    out["complete"] = out["coverage"] >= MIN_COVERAGE
    out = out[out["metered_hours"] > 0]
    receipt = make_receipt("load.load_factor", energy_kwh, parameters={"tz": tz, "freq": freq},
                           checks=checks, caveats=[
        f"Peak demand is the highest {res} interval; load factors computed at different "
        "resolutions are not comparable (finer data gives higher peaks, lower LF).",
        "Average demand uses metered hours only, so gaps don't depress the load factor; "
        "check 'coverage' and 'complete'.",
    ])
    return Result(out, receipt)
