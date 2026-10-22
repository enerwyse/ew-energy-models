"""Snapshot IESO's current Ontario demand forecasts into an open archive.

IESO keeps forecasts for only ~90 days, so the forecast-vs-actual record has to
be built forward. Run this at least daily (the scheduled GitHub workflow runs
it twice a day and commits the CSV):

    python scripts/archive_ieso_forecast.py data/ieso_forecast_archive.csv
"""

import sys

from ewmodels.data.ieso_forecast import archive_snapshot

path = sys.argv[1] if len(sys.argv) > 1 else "data/ieso_forecast_archive.csv"
print(f"appended {archive_snapshot(path)} rows to {path}")
