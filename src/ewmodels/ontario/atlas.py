"""Ontario Peak Atlas — what the ICI peak hours actually look like.

The headline statistic is *crowding*: how many days per base period came within
m% of the 5th-highest peak. A crowded season punishes forecast error, because
every near-miss day is a day an operator must decide whether to curtail.
"""

from __future__ import annotations

import pandas as pd

from ewmodels.data.ieso import EST
from ewmodels.ontario.ici import N_PEAKS, base_period_label, top_peak_hours
from ewmodels.receipts import Result

DEFAULT_MARGINS = (0.01, 0.02, 0.03, 0.05)


def daily_peaks(ontario_demand_mw: pd.Series) -> pd.DataFrame:
    """Daily maximum Ontario demand (EST calendar days) and the hour it occurred."""
    s = ontario_demand_mw.dropna().tz_convert(EST)
    day = s.index.normalize()
    g = s.groupby(day)
    out = pd.DataFrame({"peak_mw": g.max(), "peak_interval_start": g.idxmax()})
    out["base_period"] = out.index.map(base_period_label)
    return out


def peak_atlas(ontario_demand_mw: pd.Series, margins=DEFAULT_MARGINS) -> Result[dict]:
    """Per-base-period peak summary, crowding, and timing distributions.

    Returns value = {
      "peaks":    top-5 hours per base period,
      "summary":  one row per base period (threshold, distinct peak days, crowding),
      "by_hour":  count of peak hours by hour-ending (EST),
      "by_month": count of peak hours by month,
    }
    """
    peaks_res = top_peak_hours(ontario_demand_mw)
    peaks = peaks_res.value
    days = daily_peaks(ontario_demand_mw)

    rows = []
    for label, grp in peaks.groupby("base_period"):
        threshold = grp.loc[grp["rank"] == N_PEAKS, "demand_mw"].iloc[0]
        d = days[days["base_period"] == label]
        row = {
            "base_period": label,
            "complete": bool(grp["complete"].iloc[0]),
            "max_mw": grp["demand_mw"].max(),
            "threshold_mw": threshold,
            "distinct_peak_days": grp["interval_start"].dt.normalize().nunique(),
            "days_in_period": len(d),
        }
        for m in margins:
            row[f"days_within_{round(m * 100)}pct"] = int((d["peak_mw"] >= (1 - m) * threshold).sum())
        rows.append(row)
    summary = pd.DataFrame(rows)

    ts = peaks["interval_start"]
    by_hour = (ts.dt.hour + 1).value_counts().sort_index().rename_axis("hour_ending_est").rename("peak_hours")
    by_month = ts.dt.month.value_counts().sort_index().rename_axis("month").rename("peak_hours")

    receipt = peaks_res.receipt
    receipt.model = "ontario.atlas.peak_atlas"
    receipt.parameters["margins"] = list(margins)
    return Result({"peaks": peaks, "summary": summary, "by_hour": by_hour, "by_month": by_month}, receipt)
