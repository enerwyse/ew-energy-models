"""Archive IESO day-ahead Ontario zonal prices and monthly Global Adjustment, then check for gaps.

IESO keeps the daily DA price files for only ~90 days and the GA files for ~12 months,
and publishes no yearly file of post-Market-Renewal prices. This builds that record.
Each run is idempotent and back-fills anything IESO still lists.

    python scripts/archive_ieso_prices.py                  # archive + gap check
    python scripts/archive_ieso_prices.py --check-only     # gap check only

Exit code 1 = recent gaps found that are still recoverable (GitHub emails you).
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

from ewmodels.data.ieso_prices import archive_da_prices, archive_ga, da_price_gaps

ap = argparse.ArgumentParser()
ap.add_argument("--da-archive", default="data/ieso_da_price_archive.csv")
ap.add_argument("--ga-archive", default="data/ieso_ga_archive.csv")
ap.add_argument("--check-only", action="store_true")
ap.add_argument("--skip-check", action="store_true")
a = ap.parse_args()

if not a.check_only:
    print(json.dumps({"da_prices": archive_da_prices(a.da_archive), "ga": archive_ga(a.ga_archive)}, indent=2))
if a.skip_check:
    sys.exit(0)

today_est = datetime.now(timezone(timedelta(hours=-5))).date()
try:
    first = pd.to_datetime(pd.read_csv(a.da_archive, usecols=["delivery_date"])["delivery_date"]).min().date()
except (FileNotFoundError, ValueError):
    print("DA price archive is empty: run without --check-only first.")
    sys.exit(1)
gaps = da_price_gaps(a.da_archive, first, today_est)  # today's prices were published yesterday
RECOVERABLE_DAYS = 80  # IESO keeps ~90 days; leave a margin
recoverable = [d for d in gaps if (today_est - d).days < RECOVERABLE_DAYS]
permanent = [d for d in gaps if d not in recoverable]
if permanent:
    print(f"Known permanent gaps ({len(permanent)}): {', '.join(d.isoformat() for d in permanent)}")
if recoverable:
    print(f"ACTION NEEDED: {len(recoverable)} dates missing DA prices: "
          f"{', '.join(d.isoformat() for d in recoverable)}. Re-run this script now.")
    sys.exit(1)
print(f"OK: DA prices archived {first} .. {today_est}.")
