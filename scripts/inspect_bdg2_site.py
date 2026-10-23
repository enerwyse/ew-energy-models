"""Quick look at one BDG2 site's office meters: low-end readings and zero runs.

    python scripts/inspect_bdg2_site.py Lamb
"""
import sys

import pandas as pd

from ewmodels.data import bdg2

site = sys.argv[1] if len(sys.argv) > 1 else "Lamb"
meta = bdg2.metadata()
ids = meta[(meta["site_id"] == site) & (meta["primaryspaceusage"] == "Office")]["building_id"].tolist()
ids = [b for b in ids if b in set(bdg2.electricity_columns())]
for b, s in bdg2.load_buildings(ids).items():
    s = s[s.index.year == 2017]
    p90 = s.quantile(0.9)
    print(f"{b:32s} n={len(s):5d}  min={s.min():8.3f}  p01={s.quantile(.01):8.3f}  p10={s.quantile(.1):8.3f}  "
          f"p90={p90:8.2f}  zero={(s == 0).mean():5.1%}  near_zero={(s <= 0.01 * p90).mean():5.1%}")
