"""Building Data Genome Project 2 (BDG2): open hourly meter data for ~1,600
non-residential buildings, 2016-2017 (Miller et al., Scientific Data 7, 368, 2020;
CC BY 4.0). https://github.com/buds-lab/building-data-genome-project-2

Used for examples and demos only. Files are downloaded at runtime and cached;
nothing is redistributed with this package. Cite the dataset when you publish.

Timestamps in BDG2 are local wall-clock time at each site. `load_building`
localizes them with the site's timezone from the metadata; the duplicated and
skipped hours at daylight-saving changes are dropped.
"""

from __future__ import annotations

import io
import urllib.request
from pathlib import Path

import pandas as pd

_REPO = "buds-lab/building-data-genome-project-2"
_CANDIDATES = [
    "https://media.githubusercontent.com/media/{repo}/{branch}/data/{path}",
    "https://raw.githubusercontent.com/{repo}/{branch}/data/{path}",
]
ELECTRICITY = "meters/cleaned/electricity_cleaned.csv"
METADATA = "metadata/metadata.csv"
_LEGACY_TZ = {  # BDG2 uses legacy zone names some tz databases no longer ship
    "US/Eastern": "America/New_York", "US/Central": "America/Chicago",
    "US/Mountain": "America/Denver", "US/Pacific": "America/Los_Angeles",
    "US/Arizona": "America/Phoenix", "Canada/Eastern": "America/Toronto",
}
CITATION = ("Miller, C., Kathirgamanathan, A., Picchetti, B. et al. The Building Data Genome "
            "Project 2, energy meter data from the ASHRAE Great Energy Predictor III competition. "
            "Sci Data 7, 368 (2020). CC BY 4.0.")


def _download(path: str, cache_dir: Path) -> Path:
    target = cache_dir / path.replace("/", "_")
    if target.exists() and target.stat().st_size > 1000:
        return target
    cache_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    for branch in ("master", "main"):
        for tpl in _CANDIDATES:
            url = tpl.format(repo=_REPO, branch=branch, path=path)
            try:
                with urllib.request.urlopen(url, timeout=300) as r:
                    head = r.read(200)
                    if head.startswith(b"version https://git-lfs"):
                        errors.append(f"{url}: LFS pointer, not data")
                        continue
                    with target.open("wb") as f:
                        f.write(head)
                        while chunk := r.read(1 << 20):
                            f.write(chunk)
                return target
            except Exception as exc:  # try the next mirror
                errors.append(f"{url}: {exc}")
    raise RuntimeError("Could not download BDG2 file " + path + ":\n  " + "\n  ".join(errors))


def metadata(cache_dir: str | Path = "~/.cache/ewmodels/bdg2") -> pd.DataFrame:
    """Building metadata (building_id, site_id, primaryspaceusage, sqm, timezone, ...)."""
    return pd.read_csv(_download(METADATA, Path(cache_dir).expanduser()))


def electricity_path(cache_dir: str | Path = "~/.cache/ewmodels/bdg2") -> Path:
    """Local path of the cleaned hourly electricity file (downloads ~150+ MB once)."""
    return _download(ELECTRICITY, Path(cache_dir).expanduser())


def load_buildings(building_ids: list[str], cache_dir: str | Path = "~/.cache/ewmodels/bdg2") -> dict[str, pd.Series]:
    """Hourly electricity (kWh per hour) for the given buildings, each as a
    timezone-aware Series indexed by interval start."""
    meta = metadata(cache_dir).set_index("building_id")
    path = electricity_path(cache_dir)
    cols = pd.read_csv(path, nrows=0).columns
    missing = [b for b in building_ids if b not in cols]
    if missing:
        raise KeyError(f"not in BDG2 electricity data: {missing}")
    df = pd.read_csv(path, usecols=["timestamp", *building_ids], parse_dates=["timestamp"])
    out = {}
    for b in building_ids:
        tz = meta.loc[b, "timezone"] if "timezone" in meta.columns and b in meta.index else None
        if not isinstance(tz, str):
            raise ValueError(f"no timezone in BDG2 metadata for {b}")
        tz = _LEGACY_TZ.get(tz, tz)
        s = pd.Series(df[b].to_numpy(), index=df["timestamp"]).dropna()
        idx = s.index.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
        s.index = idx
        out[b] = s[s.index.notna()].rename("kwh")
    return out


def electricity_columns(cache_dir: str | Path = "~/.cache/ewmodels/bdg2") -> list[str]:
    return [c for c in pd.read_csv(electricity_path(cache_dir), nrows=0).columns if c != "timestamp"]


WEATHER = "weather/weather.csv"


def load_weather(site_ids: list[str], cache_dir: str | Path = "~/.cache/ewmodels/bdg2") -> dict[str, pd.Series]:
    """Hourly outdoor air temperature (degC) for BDG2 sites, timezone-aware.

    BDG2 weather comes from airport stations near each site (column
    `airTemperature`). Each site's timezone is taken from its buildings' metadata.
    """
    meta = metadata(cache_dir)
    w = pd.read_csv(_download(WEATHER, Path(cache_dir).expanduser()),
                    usecols=["timestamp", "site_id", "airTemperature"], parse_dates=["timestamp"])
    out = {}
    for site in site_ids:
        tzs = meta.loc[meta["site_id"] == site, "timezone"].dropna().unique()
        if len(tzs) == 0:
            raise ValueError(f"no timezone in BDG2 metadata for site {site}")
        tz = _LEGACY_TZ.get(tzs[0], tzs[0])
        sw = w[w["site_id"] == site].dropna(subset=["airTemperature"])
        s = pd.Series(sw["airTemperature"].to_numpy(dtype=float), index=sw["timestamp"])
        s = s[~s.index.duplicated(keep="first")].sort_index()
        s.index = s.index.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
        out[site] = s[s.index.notna()].rename("temp_c")
    return out
