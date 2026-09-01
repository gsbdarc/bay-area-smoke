#!/usr/bin/env python3
"""Bootstrap: download the Stanford ECHOLab smoke PM2.5 grid. ONE TIME.

This is the heavy step -- up to ~1.8 GB -- and it is deliberately NOT part of
the scheduled refresh. Run it once on a machine with disk and bandwidth; the
small Bay Area subset it produces is committed to the repo, so the pipeline
never needs to repeat it.

Two sources, in preference order:

  v2 (through 2023) -- Dropbox. Longer coverage, but the link is rotatable and
      is not a citable stable URL. See issue #5.
  v1 (through 2020) -- Harvard Dataverse, doi:10.7910/DVN/DJVMTV. Stable, and
      the fallback if v2 is unreachable.

Whichever succeeded is recorded in data/processed/echolab_source.json, because
it changes where our own extension has to start.
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import PROCESSED, RAW  # noqa: E402
from util import download, log, try_download  # noqa: E402

ECHO_RAW = RAW / "echolab"

# v2 beta, 10 km grid, 2006-01-01..2023-12-31. Folder-level link: per-file deep
# links return "No Access", so the whole folder zip must be pulled.
V2_10KM_ZIP = (
    "https://www.dropbox.com/scl/fo/91k0aq80vp57qixkm508q/"
    "AFwRS_BfKyaayrr9J1mU0PY/10km_grid"
    "?rlkey=nutebc9pn2vsupr0p9ks4k73u&dl=1"
)

# v1, 10 km grid, 2006-01-01..2020-12-31. `format=original` matters: without it
# Dataverse serves an "ingested" .tab conversion instead of the original CSV.
DATAVERSE = "https://dataverse.harvard.edu/api/access/datafile"
V1_10KM_CSV = f"{DATAVERSE}/8550337?format=original"

# The 10 km grid geometry. grid_id_10km joins to the shapefile's `ID` column.
GRID_SHAPEFILE_PARTS = {
    "10km_grid_wgs84.shp": f"{DATAVERSE}/8550317",
    "10km_grid_wgs84.dbf": f"{DATAVERSE}/8550315",
    "10km_grid_wgs84.shx": f"{DATAVERSE}/8550318",
    "10km_grid_wgs84.prj": f"{DATAVERSE}/8550314",
}


def fetch_grid_geometry() -> Path:
    """The shapefile is small (~14 MB) and needed regardless of version."""
    log("grid geometry (Harvard Dataverse)")
    for name, url in GRID_SHAPEFILE_PARTS.items():
        download(url, ECHO_RAW / name)
    return ECHO_RAW / "10km_grid_wgs84.shp"


def fetch_predictions() -> tuple[Path, str]:
    """Return (csv_path, version). Tries v2, falls back to v1."""
    log("smoke PM2.5 predictions -- trying v2 (through 2023)")
    v2_zip = try_download(V2_10KM_ZIP, ECHO_RAW / "v2_10km_grid.zip", timeout=1800)

    if v2_zip is not None and zipfile.is_zipfile(v2_zip):
        with zipfile.ZipFile(v2_zip) as z:
            csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if csvs:
                target = ECHO_RAW / Path(csvs[0]).name
                if not target.exists():
                    log(f"extracting {csvs[0]}")
                    with z.open(csvs[0]) as src, open(target, "wb") as dst:
                        while chunk := src.read(1 << 20):
                            dst.write(chunk)
                return target, "v2_thru_2023"
        log("v2 zip contained no CSV -- falling back to v1")
    else:
        log("v2 unavailable (Dropbox link may have rotated -- see issue #5)")

    log("falling back to v1 (through 2020, stable Dataverse DOI)")
    return (
        download(V1_10KM_CSV, ECHO_RAW / "v1_10km_grid.csv", timeout=1800),
        "v1_thru_2020",
    )


def main() -> int:
    ECHO_RAW.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)

    fetch_grid_geometry()
    csv_path, version = fetch_predictions()

    meta = {
        "version": version,
        "predictions_file": csv_path.name,
        "bytes": csv_path.stat().st_size,
        "citation": (
            "Childs et al. (2022), Environ. Sci. Technol. 56(19):13607-13621, "
            "doi:10.1021/acs.est.2c02934. Data CC BY-SA 4.0."
        ),
    }
    (PROCESSED / "echolab_source.json").write_text(json.dumps(meta, indent=2) + "\n")
    log(f"using ECHOLab {version}")
    log("NOTE: our own extension must start the year after this version ends.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
