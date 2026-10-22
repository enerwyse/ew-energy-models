"""Archive IESO's day-ahead Ontario demand forecasts, then check for gaps.

IESO keeps every forecast version for only ~45 days, so this must run regularly.
Each run is idempotent and back-fills anything still inside IESO's window, so a
missed day heals itself if the next run happens within ~6 weeks.

    python scripts/archive_ieso_forecast.py                  # backfill + gap check
    python scripts/archive_ieso_forecast.py --check-only     # gap check only

Exit code 1 = gaps found (GitHub emails you when the workflow fails).
"""

import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta, timezone

from ewmodels.data.ieso_backfill import backfill, coverage_gaps

ARCHIVE = "data/ieso_forecast_archive.csv"

ap = argparse.ArgumentParser()
ap.add_argument("--archive", default=ARCHIVE)
ap.add_argument("--check-only", action="store_true")
ap.add_argument("--skip-check", action="store_true")
a = ap.parse_args()

if not a.check_only:
    print(json.dumps(backfill(a.archive), indent=2))
if a.skip_check:
    sys.exit(0)

today_est = datetime.now(timezone(timedelta(hours=-5))).date()
try:  # gaps are checked from the first date we ever captured a day-ahead forecast for
    with open(a.archive) as f:
        START = min(date.fromisoformat(r["delivery_date"]) for r in csv.DictReader(f)
                    if "d1_1600" in r["cutoff"].split("+"))
except (FileNotFoundError, ValueError):
    print("Archive is empty: run without --check-only first.")
    sys.exit(1)
gaps = coverage_gaps(a.archive, START, today_est)
RECOVERABLE_DAYS = 40  # IESO keeps versions ~45 days; leave a safety margin
recoverable = [d for d in gaps if (today_est - d).days < RECOVERABLE_DAYS]
permanent = [d for d in gaps if d not in recoverable]
if permanent:
    print(f"Known permanent gaps ({len(permanent)}; IESO no longer has these versions): "
          f"{', '.join(d.isoformat() for d in permanent)}")
if recoverable:
    print(f"ACTION NEEDED: {len(recoverable)} recent dates missing a day-ahead forecast: "
          f"{', '.join(d.isoformat() for d in recoverable)}")
    print("These are still recoverable: re-run this script (or the GitHub workflow) now.")
    sys.exit(1)
print(f"OK: day-ahead forecasts archived through {today_est} (since {START}).")
