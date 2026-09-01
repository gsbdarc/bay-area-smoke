#!/usr/bin/env python3
"""Bootstrap: find our 10 km grid cells and subset ECHOLab to the Bay Area.

Two jobs:
  1. Point-in-polygon each of our ten locations against the 10 km grid to get
     its `grid_id_10km`.
  2. Stream the (very large) national predictions CSV once, keeping only rows
     for those ten cells.

The result is small enough to commit, which is what frees the scheduled
refresh from ever touching Dropbox or Dataverse again. See issue #5.

CRITICAL -- the file is sparsely encoded. Per the official README:

    "All rows in this file are predictions on smoke days. Predictions on
     non-smoke days are by construction 0 ug/m^3 and not included."

So an ABSENT (cell, date) row means non-smoke day = 0, an EXPLICIT 0 means
smoke was overhead but PM2.5 was not elevated, and a BLANK value means NA. We
preserve the sparsity here and expand it in s04, where we know the date range.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import shapefile  # pyshp
from shapely.geometry import Point, shape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import LOCATIONS, PROCESSED, RAW  # noqa: E402
from util import expect, log  # noqa: E402

ECHO_RAW = RAW / "echolab"
GRID_SHP = ECHO_RAW / "10km_grid_wgs84.shp"
CELLS_OUT = PROCESSED / "grid_cells.csv"
SUBSET_OUT = PROCESSED / "echolab_bayarea.csv.gz"

CHUNK = 2_000_000


def find_cells() -> pd.DataFrame:
    """Map each location to the grid cell containing it."""
    expect(GRID_SHP.exists(), f"{GRID_SHP} missing -- run b01_fetch_echolab.py first")

    log(f"reading {GRID_SHP.name}")
    reader = shapefile.Reader(str(GRID_SHP))
    fields = [f[0] for f in reader.fields[1:]]
    expect("ID" in fields, f"expected an 'ID' field in the grid shapefile, got {fields}")
    id_idx = fields.index("ID")

    wanted = [(loc, Point(loc.lon, loc.lat)) for loc in LOCATIONS]
    found: dict[str, int] = {}
    nearest: dict[str, tuple[float, int]] = {}

    for sr in reader.iterShapeRecords():
        bbox = sr.shape.bbox  # (xmin, ymin, xmax, ymax) -- cheap pre-filter
        geom = None
        for loc, pt in wanted:
            if loc.slug in found:
                continue
            if not (bbox[0] <= pt.x <= bbox[2] and bbox[1] <= pt.y <= bbox[3]):
                continue
            if geom is None:
                geom = shape(sr.shape.__geo_interface__)
            if geom.contains(pt) or geom.touches(pt):
                found[loc.slug] = int(sr.record[id_idx])

        # Track the nearest cell centroid as a fallback for any coastal point
        # that lands just outside the grid -- Half Moon Bay and Point Reyes are
        # close enough to the edge for this to matter.
        if len(found) < len(wanted):
            cx = (bbox[0] + bbox[2]) / 2
            cy = (bbox[1] + bbox[3]) / 2
            for loc, pt in wanted:
                if loc.slug in found:
                    continue
                d = (cx - pt.x) ** 2 + (cy - pt.y) ** 2
                if loc.slug not in nearest or d < nearest[loc.slug][0]:
                    nearest[loc.slug] = (d, int(sr.record[id_idx]))

    rows = []
    for loc in LOCATIONS:
        if loc.slug in found:
            rows.append((loc.slug, found[loc.slug], "contains"))
        elif loc.slug in nearest:
            log(f"WARNING: {loc.slug} not inside any cell; using nearest centroid")
            rows.append((loc.slug, nearest[loc.slug][1], "nearest"))
        else:
            raise SystemExit(f"no grid cell found for {loc.slug}")

    df = pd.DataFrame(rows, columns=["location", "grid_id_10km", "match"])
    expect(
        df["grid_id_10km"].nunique() == len(df),
        "two locations resolved to the same 10 km cell -- they would show "
        f"identical smoke series:\n{df}",
    )
    return df


def subset_predictions(cells: pd.DataFrame) -> pd.DataFrame:
    """Stream the national CSV, keeping only our cells."""
    meta_path = PROCESSED / "echolab_source.json"
    expect(meta_path.exists(), "echolab_source.json missing -- run b01 first")
    meta = json.loads(meta_path.read_text())
    csv_path = ECHO_RAW / meta["predictions_file"]
    expect(csv_path.exists(), f"{csv_path} missing -- run b01 first")

    keep = set(cells["grid_id_10km"])
    log(f"streaming {csv_path.name} for {len(keep)} cells (this takes a few minutes)")

    parts, seen = [], 0
    for chunk in pd.read_csv(
        csv_path,
        chunksize=CHUNK,
        dtype={"grid_id_10km": "Int64", "date": "string", "smokePM_pred": "float64"},
    ):
        seen += len(chunk)
        parts.append(chunk[chunk["grid_id_10km"].isin(keep)])
        print(f"    {seen:,} rows scanned", end="\r", flush=True)
    print()

    out = pd.concat(parts, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"], format="%Y%m%d")
    out = out.merge(cells[["location", "grid_id_10km"]], on="grid_id_10km", how="left")

    log(f"kept {len(out):,} of {seen:,} rows")
    expect(len(out) > 0, "no rows matched our grid cells -- check the ID join")
    return out[["location", "date", "smokePM_pred"]].sort_values(["location", "date"])


def main() -> int:
    PROCESSED.mkdir(parents=True, exist_ok=True)

    cells = find_cells()
    cells.to_csv(CELLS_OUT, index=False)
    log(f"wrote {CELLS_OUT.name}")
    for r in cells.itertuples(index=False):
        log(f"  {r.location:15s} cell {r.grid_id_10km} ({r.match})")

    subset = subset_predictions(cells)
    subset.to_csv(SUBSET_OUT, index=False, compression="gzip", float_format="%.4f")
    log(f"wrote {SUBSET_OUT.name} ({SUBSET_OUT.stat().st_size:,} bytes)")

    # Sparsity sanity check: a location with rows on more than ~40% of days
    # would mean we have misread the encoding, since smoke days are the
    # exception even in the worst years.
    span = (subset["date"].max() - subset["date"].min()).days + 1
    for loc, g in subset.groupby("location"):
        frac = len(g) / span
        log(f"  {loc:15s} {len(g):6,} smoke-day rows ({100 * frac:4.1f}% of span)")
        expect(
            frac < 0.5,
            f"{loc} has rows on {100 * frac:.0f}% of days. The file should only "
            "contain smoke days -- this suggests the sparse encoding was "
            "misread, or a wrong cell was joined.",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
