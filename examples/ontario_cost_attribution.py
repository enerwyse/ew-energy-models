"""Ontario Class A cost attribution on real IESO data (catalogue Model 3).

    python examples/ontario_cost_attribution.py | tee out/cost_output.txt

Needs the open price archives in data/ (built by scripts/archive_ieso_prices.py)
and downloads IESO demand and LFDA. The sites are synthetic archetypes, so the
numbers show how the cost structure works, not any real customer's bill.
"""

from datetime import date
from pathlib import Path

import pandas as pd

from ewmodels.data.ieso import EST, fetch_demand
from ewmodels.data.ieso_prices import fetch_lfda, load_da_prices, load_ga, oemp
from ewmodels.ontario import cost, ici

pd.set_option("display.width", 200)
OUT = Path("out")
OUT.mkdir(exist_ok=True)
today = date.today()


def section(title):
    print(f"\n=== {title} ===")


demand = fetch_demand(range(2024, today.year + 1))["ontario_demand_mw"]
demand = demand[demand.index >= "2024-05-01 00:00:00-05:00"]
ga = load_ga("data/ieso_ga_archive.csv")
da = load_da_prices("data/ieso_da_price_archive.csv")
px = oemp(da, fetch_lfda(sorted({da.index.min().year, da.index.max().year})))
print(f"Demand {demand.index.min():%Y-%m-%d} .. {demand.index.max():%Y-%m-%d}; "
      f"DA prices {da.index.min():%Y-%m-%d} .. {da.index.max():%Y-%m-%d}; "
      f"GA months {ga.index.min()} .. {ga.index.max()}")

# 1. What one MWh in a peak hour costs ------------------------------------------------
section("1. GA paid per MWh consumed in a base period's 5 peak hours")
peaks = ici.top_peak_hours(demand).value
print(peaks[["base_period", "rank", "interval_start", "demand_mw"]].to_string(index=False))
phv = cost.peak_hour_value(demand, ga)
t = phv.value
t["per_month_avg"] = t["ga_total_dollars"] / t["months_with_ga"].where(t["months_with_ga"] > 0)
t["dollars_per_kwh_at_peak"] = t["dollars_per_mwh_at_peak"] / 1000
print(t.to_string(index=False))
avg_oemp = px["oemp"].mean()
print(f"\nAverage OEMP over the archived window: ${avg_oemp:.2f}/MWh (${avg_oemp / 1000:.4f}/kWh)")
for _, r in t[t.months_with_ga > 0].iterrows():
    print(f"{r.base_period}: 1 kWh in the peak hours -> ${r.dollars_per_kwh_at_peak:,.2f} of GA over "
          f"{r.months_with_ga} months of its adjustment period = {r.dollars_per_mwh_at_peak / avg_oemp:,.0f}x "
          f"the average hourly energy price")
print("\n" + phv.receipt.summary())
t.to_csv(OUT / "cost_peak_hour_value.csv", index=False)

# 2. Three archetype sites, 1 MW average -----------------------------------------------
idx = pd.date_range("2024-05-01", "2026-11-01", freq="1h", inclusive="left", tz=EST)
local = idx.tz_convert("America/Toronto")
on = (local.day_of_week < 5) & (local.hour >= 6) & (local.hour < 22)
flat = pd.Series(1000.0, index=idx)
shift = pd.Series(300.0, index=idx).where(~on, 1700.0)  # two shifts, weekdays 06:00-22:00
shift *= 1000.0 / shift.mean()
peak_hours = pd.DatetimeIndex(peaks["interval_start"])
curtail = flat.copy()
curtail[curtail.index.isin(peak_hours)] = 500.0  # halves load in the 5 peak hours (perfect foresight)
sites = {"24/7 flat": flat, "two-shift weekdays": shift, "24/7, curtails 50% at peaks": curtail}

section("2. Monthly commodity bill, Class A vs Class B (full months with prices and GA)")
rows = []
for name, kwh in sites.items():
    pdf = cost.site_pdf(kwh, demand)
    res = cost.bill_breakdown(kwh, px, ga, pdf.value)
    b = res.value
    b.insert(0, "site", name)
    rows.append(b)
    print(f"\n{name}: PDF {', '.join(f'{k} {v:.3e}' for k, v in pdf.value.items())}")
    print(b[["month", "kwh", "avg_oemp", "energy_dollars", "ga_class_a_dollars", "ga_class_b_dollars",
             "total_class_a", "total_class_b", "ga_share_class_a"]].round(3).to_string(index=False))
    if name == "24/7 flat":
        print("\n" + res.receipt.summary())
bills = pd.concat(rows)
bills.to_csv(OUT / "cost_bills.csv", index=False)

section("3. Summary over the billed months")
s = bills.groupby("site")[["kwh", "energy_dollars", "ga_class_a_dollars", "ga_class_b_dollars",
                           "total_class_a", "total_class_b"]].sum()
s["ga_share_class_a"] = s["ga_class_a_dollars"] / s["total_class_a"]
s["class_a_saves_vs_b"] = 1 - s["total_class_a"] / s["total_class_b"]
s["cents_per_kwh_class_a"] = s["total_class_a"] / s["kwh"] * 100
print(s.round(3).to_string())
s.to_csv(OUT / "cost_summary.csv")
