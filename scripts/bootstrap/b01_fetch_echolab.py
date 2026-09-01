"""Bootstrap 01 -- fetch the Stanford ECHOLab smoke PM2.5 product and its grid.

**One-time, and deliberately not part of the scheduled refresh.** This is the
only stage that moves multiple gigabytes. Its useful output -- a Bay-Area-sized
extract -- is committed to the repo, so the monthly GitHub Actions job never
touches Dropbox and never re-downloads any of this.

## Two sources, in preference order

**v2.0 beta (Dropbox), 2006-01-01 -> 2023-12-31.** The lab's current product.
It is a shared *folder*, and `&dl=1` yields a single 9.2 GB zip of the whole
folder -- there is no per-file URL. That is 5x the plan's 1.8 GB estimate
because the bundle also carries county/tract/ZCTA aggregations and shapefiles
we do not use. We take it anyway: it is three more years of *published* smoke
than the fallback, which directly shrinks the span we have to model ourselves.

**v1 (Harvard Dataverse), 2006-01-01 -> 2020-12-31.** A 1.78 GB CSV behind a
stable DOI (`10.7910/DVN/DJVMTV`). Used automatically if Dropbox fails.

Dropbox links rot and DOIs do not, so which path ran is recorded in
`data/raw/echolab/PROVENANCE.json` and reported by `run_all.py`. The difference
is visible in the finished site: with v2 our own extension starts in 2024, with
v1 it has to start in 2021.

The 10 km grid shapefile always comes from Dataverse -- it is small, stable, and
identical between versions.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import RAW  # noqa: E402
from fetch import download, human, log, require_space  # noqa: E402

ECHO = RAW / "echolab"
PROVENANCE = ECHO / "PROVENANCE.json"

DATAVERSE = "https://dataverse.harvard.edu/api/access/datafile"

# Grid geometry: grid_id_10km -> polygon. Dataverse file ids, verified against
# the dataset's file listing on 2026-08-31.
GRID_FILES = {
    "10km_grid_wgs84.shp": 8550317,
    "10km_grid_wgs84.dbf": 8550315,
    "10km_grid_wgs84.shx": 8550318,
    "10km_grid_wgs84.prj": 8550314,
}

# v1 daily 10 km predictions, 2006-01-01 -> 2020-12-31.
V1_CSV_ID = 8550337
V1_CSV_NAME = "smokePM2pt5_predictions_daily_10km_20060101-20201231.csv"

# v2.0 beta shared folder. `dl=1` turns the folder into a zip download.
V2_URL = (
    "https://www.dropbox.com/scl/fo/91k0aq80vp57qixkm508q/"
    "AKQSIJ5C1kDMQLz8oh02UAA"
    "?rlkey=nutebc9pn2vsupr0p9ks4k73u&dl=1"
)
V2_NAME = "version2.0_thru_2023.zip"

# Coverage each path buys us, and therefore where our own extension has to
# start. Consumed by b02 and reported in the README.
COVERAGE = {
    "v2": {"first": "2006-01-01", "last": "2023-12-31", "extend_from": "2024-01-01"},
    "v1": {"first": "2006-01-01", "last": "2020-12-31", "extend_from": "2021-01-01"},
}


def fetch_grid(force: bool = False) -> None:
    log("[b01] 10 km grid shapefile (Harvard Dataverse)")
    for name, fid in GRID_FILES.items():
        download(
            f"{DATAVERSE}/{fid}?format=original",
            ECHO / name,
            force=force,
            expect_min_bytes=100,
            timeout=120,
        )


def fetch_smoke(prefer: str = "v2", force: bool = False) -> str:
    """Fetch the smoke product. Returns the version actually obtained."""
    if prefer == "v2":
        log("[b01] ECHOLab v2.0 beta, 2006-2023 (Dropbox, ~9.2 GB)")
        require_space(ECHO, need_gb=12.0)
        try:
            download(V2_URL, ECHO / V2_NAME, force=force,
                     expect_min_bytes=100 * 1024 * 1024, timeout=180)
            return "v2"
        except RuntimeError as exc:
            log(f"[b01] Dropbox v2 failed ({exc}).")
            log("[b01] Falling back to Dataverse v1 -- our own extension will "
                "then have to cover 2021 onward instead of 2024 onward.")

    log("[b01] ECHOLab v1, 2006-2020 (Harvard Dataverse, ~1.8 GB)")
    require_space(ECHO, need_gb=4.0)
    download(
        f"{DATAVERSE}/{V1_CSV_ID}?format=original",
        ECHO / V1_CSV_NAME,
        force=force,
        expect_min_bytes=100 * 1024 * 1024,
        timeout=180,
    )
    return "v1"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", choices=("v2", "v1"), default="v2",
                    help="which ECHOLab product to prefer (default: v2)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--grid-only", action="store_true",
                    help="fetch just the shapefile; skip the multi-GB product")
    args = ap.parse_args(argv)

    ECHO.mkdir(parents=True, exist_ok=True)
    fetch_grid(force=args.force)

    if args.grid_only:
        log("[b01] --grid-only: stopping before the large download.")
        return 0

    version = fetch_smoke(prefer=args.version, force=args.force)

    PROVENANCE.write_text(json.dumps({
        "version": version,
        "source": "dropbox-v2-beta" if version == "v2" else "harvard-dataverse-v1",
        "url": V2_URL.split("?")[0] if version == "v2"
              else f"https://doi.org/10.7910/DVN/DJVMTV",
        "file": V2_NAME if version == "v2" else V1_CSV_NAME,
        "fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        **COVERAGE[version],
        "license": "CC BY-SA 4.0",
        "cite": (
            "Childs, M.L., et al. (2022). Daily local-level estimates of "
            "ambient wildfire smoke PM2.5 for the contiguous US. "
            "Environmental Science & Technology 56(19):13607-13621."
        ),
    }, indent=2) + "\n")

    total = sum(p.stat().st_size for p in ECHO.iterdir() if p.is_file())
    log(f"[b01] done: ECHOLab {version}, published smoke through "
        f"{COVERAGE[version]['last']}; our extension starts "
        f"{COVERAGE[version]['extend_from']}. {human(total)} in {ECHO}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
