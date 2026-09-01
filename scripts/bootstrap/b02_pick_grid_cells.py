"""Bootstrap 02 -- pick our ten 10 km grid cells and extract their smoke series.

Two jobs, both one-time, both producing small committed artifacts so that no
later run needs the multi-gigabyte source:

1. `data/processed/grid_cells.csv` -- which grid cell contains each location.
2. `data/processed/echolab_smokepm.parquet` -- the published daily smoke PM2.5
   for just those ten cells, densified.

The shapefile's `ID` column is the join key: it corresponds to `grid_id_10km`
in the prediction files (stated in ECHOLab's README).

## Densifying the sparse encoding -- the step that silently ruins everything

ECHOLab's files contain **only smoke days**. Three cases, and they are not
interchangeable:

| In the file | Meaning | Value |
|---|---|---|
| row absent | non-smoke day | **0.0** |
| row present, value `0` | smoke overhead, PM2.5 not elevated | **0.0** |
| row present, value empty | genuinely missing | **NaN** |

Reading the CSV and taking it at face value understates smoke-day *frequency*
(the absent zeros never appear) while overstating mean smoke PM2.5 (the average
is taken over smoke days only). Both errors point the same way -- toward a
prettier calendar than the truth -- so neither would look obviously wrong on the
finished page. Hence the explicit `expected == n_cells * n_days` assertion
below.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import zipfile
from pathlib import Path

import pandas as pd
import shapefile
import shapely
from shapely.geometry import shape as shapely_shape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import LOCATIONS, PROCESSED, RAW  # noqa: E402
from fetch import log  # noqa: E402

ECHO = RAW / "echolab"
GRID_SHP = ECHO / "10km_grid_wgs84.shp"
CELLS_OUT = PROCESSED / "grid_cells.csv"
SMOKE_OUT = PROCESSED / "echolab_smokepm.parquet"

# Member inside the v2 folder zip. Matched by suffix so a reshuffled folder
# layout still resolves.
V2_MEMBER_HINT = "smokePM2pt5_predictions_daily_10km"


def load_provenance() -> dict:
    p = ECHO / "PROVENANCE.json"
    if not p.exists():
        raise SystemExit(
            "b02: data/raw/echolab/PROVENANCE.json missing -- run b01 first."
        )
    return json.loads(p.read_text())


def pick_cells() -> pd.DataFrame:
    """Find the grid cell containing each location point.

    Coastal points need care. The ECHOLab grid covers the contiguous US land
    surface, and Point Reyes and Half Moon Bay sit close enough to its edge
    that a point can land just outside every cell. Failing the build there
    would be the wrong call -- the nearest cell is a perfectly good estimate a
    few kilometres away -- so we fall back to it and record `match='nearest'`,
    which is carried through to the site's methods section rather than being
    quietly absorbed.

    Boundary points count as inside: `covers` rather than `contains`, since a
    location sitting exactly on a cell edge is in the cell, not nowhere.
    """
    if not GRID_SHP.exists():
        raise SystemExit(f"b02: {GRID_SHP} missing -- run b01 first.")

    reader = shapefile.Reader(str(GRID_SHP.with_suffix("")))
    fields = [f[0] for f in reader.fields[1:]]
    if "ID" not in fields:
        raise SystemExit(f"b02: grid shapefile has no 'ID' field; got {fields}")
    id_idx = fields.index("ID")

    wanted = [(loc.slug, loc.lon, loc.lat) for loc in LOCATIONS]
    found: dict[str, dict] = {}
    nearest: dict[str, tuple[float, dict]] = {}

    for sr in reader.iterShapeRecords():
        bbox = sr.shape.bbox
        cell_id = int(sr.record[id_idx])
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2

        cands = [
            (slug, lon, lat) for slug, lon, lat in wanted
            if slug not in found
            and bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]
        ]
        if cands:
            geom = shapely_shape(sr.shape.__geo_interface__)
            c = geom.centroid
            for slug, lon, lat in cands:
                # `covers` includes the boundary; `contains` would not.
                if shapely.covers(geom, shapely.Point(lon, lat)):
                    found[slug] = {
                        "slug": slug,
                        "grid_id_10km": cell_id,
                        "cell_lon": round(c.x, 6),
                        "cell_lat": round(c.y, 6),
                        "match": "contains",
                    }

        # Keep the best near-miss for anything still unresolved.
        for slug, lon, lat in wanted:
            if slug in found:
                continue
            d2 = (cx - lon) ** 2 + (cy - lat) ** 2
            if slug not in nearest or d2 < nearest[slug][0]:
                nearest[slug] = (d2, {
                    "slug": slug,
                    "grid_id_10km": cell_id,
                    "cell_lon": round(cx, 6),
                    "cell_lat": round(cy, 6),
                    "match": "nearest",
                })

    rows = []
    for loc in LOCATIONS:
        if loc.slug in found:
            rows.append(found[loc.slug])
        elif loc.slug in nearest:
            rec = nearest[loc.slug][1]
            km = (nearest[loc.slug][0] ** 0.5) * 111.0
            log(f"[b02] NOTE: {loc.slug} falls outside every grid cell "
                f"(coastal edge); using nearest cell {rec['grid_id_10km']}, "
                f"~{km:.1f} km away.")
            rows.append(rec)
        else:
            raise SystemExit(f"b02: no grid cell resolvable for {loc.slug}")

    df = pd.DataFrame(rows)
    if df["grid_id_10km"].duplicated().any():
        dupes = df[df["grid_id_10km"].duplicated(keep=False)]
        log("[b02] WARNING: two locations share one 10 km cell and will show "
            "identical smoke series:\n" + dupes.to_string(index=False))
    return df


def _open_predictions():
    """Yield the prediction CSV as a text stream, from zip or plain file."""
    prov = load_provenance()
    path = ECHO / prov["file"]
    if not path.exists():
        raise SystemExit(f"b02: {path} missing -- run b01 first.")

    if path.suffix == ".zip":
        z = zipfile.ZipFile(path)
        members = [
            n for n in z.namelist()
            if V2_MEMBER_HINT in Path(n).name and n.lower().endswith(".csv")
        ]
        if not members:
            raise SystemExit(
                f"b02: no 10 km daily CSV inside {path.name}. Members like:\n  "
                + "\n  ".join(sorted(z.namelist())[:25])
            )
        member = min(members, key=len)
        log(f"[b02] reading {member} from {path.name}")
        return prov, io.TextIOWrapper(z.open(member), encoding="utf-8")

    log(f"[b02] reading {path.name}")
    return prov, path.open("r", encoding="utf-8", newline="")


def extract_smoke(cells: pd.DataFrame) -> pd.DataFrame:
    """Stream the national prediction file, keeping only our ten cells."""
    prov, stream = _open_predictions()
    id_to_slug = dict(zip(cells["grid_id_10km"], cells["slug"]))
    wanted = {str(k) for k in id_to_slug}

    rows: list[tuple[str, str, float]] = []
    n_read = 0
    n_blank = 0

    with stream as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames or []
        need = {"grid_id_10km", "date", "smokePM_pred"}
        if not need.issubset(cols):
            raise SystemExit(f"b02: prediction CSV columns are {cols}, need {need}")

        for rec in reader:
            n_read += 1
            gid = rec["grid_id_10km"]
            if gid not in wanted:
                continue
            raw = (rec["smokePM_pred"] or "").strip()
            if raw == "":
                # Genuinely missing -- NOT a zero. See module docstring.
                val = float("nan")
                n_blank += 1
            else:
                val = float(raw)
            rows.append((id_to_slug[int(gid)], rec["date"], val))

    log(f"[b02] scanned {n_read:,} national rows -> {len(rows):,} for our cells "
        f"({n_blank} blank values kept as NaN)")

    sparse = pd.DataFrame(rows, columns=["location", "date", "smoke_pm"])
    # ECHOLab writes dates as YYYYMMDD in v1 and ISO in v2; accept both.
    sparse["date"] = pd.to_datetime(
        sparse["date"].astype(str).str.replace("-", "", regex=False),
        format="%Y%m%d",
    )

    first, last = pd.Timestamp(prov["first"]), pd.Timestamp(prov["last"])
    days = pd.date_range(first, last, freq="D")

    # Densify: every cell x every day in the published range.
    full = pd.MultiIndex.from_product(
        [[loc.slug for loc in LOCATIONS], days], names=["location", "date"]
    ).to_frame(index=False)

    out = full.merge(sparse, on=["location", "date"], how="left")

    # Absent row == non-smoke day == exactly 0. Rows that were present but
    # blank are already NaN and must stay NaN, so fill only where the row was
    # genuinely missing from the source.
    present = sparse.set_index(["location", "date"]).index
    was_present = pd.MultiIndex.from_frame(out[["location", "date"]]).isin(present)
    out.loc[~was_present, "smoke_pm"] = 0.0

    expected = len(LOCATIONS) * len(days)
    if len(out) != expected:
        raise SystemExit(f"b02: densified panel is {len(out)}, expected {expected}")

    out["smoke_source"] = f"echolab-{prov['version']}"
    log(f"[b02] densified to {len(out):,} location-days "
        f"({first.date()} -> {last.date()}); "
        f"{(out['smoke_pm'] > 0).mean():.1%} of days have smoke > 0")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cells-only", action="store_true",
                    help="write grid_cells.csv and stop")
    args = ap.parse_args(argv)

    PROCESSED.mkdir(parents=True, exist_ok=True)

    log("[b02] locating 10 km grid cells for the ten locations")
    cells = pick_cells()
    cells.to_csv(CELLS_OUT, index=False)
    log(f"[b02] wrote {CELLS_OUT.name}\n" + cells.to_string(index=False))

    if args.cells_only:
        return 0

    smoke = extract_smoke(cells)
    smoke.to_parquet(SMOKE_OUT, index=False)
    log(f"[b02] wrote {SMOKE_OUT.name} ({len(smoke):,} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
