"""Stage 03 -- NOAA HMS smoke plumes -> a daily smoke/no-smoke flag per location.

HMS (Hazard Mapping System) analysts draw smoke-plume polygons over satellite
imagery. We download the annual shapefile bundles (2005 -> present) and ask, for
each of our ten points on each day: was any plume drawn over it?

Field layout, verified against `hms_smoke2020.shp`:
`Satellite, Start, End, Density`, WGS84 polygons, `Start`/`End` encoded
`YYYYDDD HHMM` (zero-padded day-of-year, then UTC time).

## Why absent dates become NaN rather than 0

A day with no polygon over Point Reyes is a clean day. A day with no polygon
*anywhere in North America* is a day HMS did not publish -- and calling that
"clean" would quietly turn outages into good air.

We can tell the two apart because the annual bundle is national: 2020 contains
polygons on all 366 dates, so a date missing from the bundle is a missing
analysis, not a quiet sky. Those dates get `plume = NaN`, which `smoke.py`
propagates into a NaN smoke value rather than a zero.

## The caveat this stage cannot fix

HMS sees a vertical column from above. A plume overhead is not the same as
smoke at the surface -- 2020-09-09, San Francisco's orange-sky day, is the
canonical illustration. That is exactly why the plume flag is only ever used as
a *gate* on a measured PM2.5 anomaly, never as a smoke estimate in itself.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import logging
import sys
import zipfile
from pathlib import Path

import pandas as pd
import shapefile
import shapely
from shapely.geometry import shape as shapely_shape

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import HMS_FIRST_DATE, LOCATIONS, PROCESSED, RAW  # noqa: E402
from fetch import download, log  # noqa: E402

# The `www.ospo.noaa.gov` mirror named in some documentation 404s; this is the
# host that actually serves the bundles (verified 2026-08-31).
BASE = (
    "https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/"
    "Smoke_Polygons/Shapefile/Annual_Bundles"
)
OUT = PROCESSED / "smoke_days.parquet"

HMS_FIRST_YEAR = 2005

# pyshp warns on every hand-drawn polygon whose rings are wound the wrong way.
# There are thousands per year, we repair them with buffer(0) below, and the
# noise buries the actual progress output.
logging.getLogger("shapefile").setLevel(logging.ERROR)

# Ordered worst-last, so `max` over a day gives the heaviest plume seen.
DENSITY_RANK = {"Light": 1, "Medium": 2, "Heavy": 3}
RANK_DENSITY = {v: k for k, v in DENSITY_RANK.items()}

# Generous box around the ten locations. Only a cheap reject filter -- anything
# that passes still gets an exact point-in-polygon test.
REGION_PAD = 0.5


def region_bbox() -> tuple[float, float, float, float]:
    lons = [loc.lon for loc in LOCATIONS]
    lats = [loc.lat for loc in LOCATIONS]
    return (
        min(lons) - REGION_PAD, min(lats) - REGION_PAD,
        max(lons) + REGION_PAD, max(lats) + REGION_PAD,
    )


def parse_hms_date(value: str) -> dt.date | None:
    """`'2020001 1546'` -> date(2020, 1, 1). Returns None if unparseable."""
    token = str(value).strip().split()
    if not token or len(token[0]) != 7 or not token[0].isdigit():
        return None
    try:
        return dt.datetime.strptime(token[0], "%Y%j").date()
    except ValueError:
        return None


def _open_bundle(path: Path, year: int) -> shapefile.Reader:
    """Read the shapefile straight out of the zip -- no extraction to disk."""
    z = zipfile.ZipFile(path)
    stem = f"hms_smoke{year}"
    members = {Path(n).suffix.lower(): n for n in z.namelist()
               if Path(n).stem == stem}
    missing = {".shp", ".dbf", ".shx"} - set(members)
    if missing:
        raise RuntimeError(f"{path.name}: bundle missing {sorted(missing)}")
    return shapefile.Reader(
        shp=io.BytesIO(z.read(members[".shp"])),
        dbf=io.BytesIO(z.read(members[".dbf"])),
        shx=io.BytesIO(z.read(members[".shx"])),
    )


def scan_year(year: int, *, force: bool = False):
    """Return (hits, dates_present) for one annual bundle.

    `hits` maps (slug, date) -> worst density rank seen.
    `dates_present` is every date the bundle has a polygon on, anywhere in the
    national domain -- our evidence that HMS ran that day at all.
    """
    name = f"hms_smoke{year}.zip"
    path = download(f"{BASE}/{name}", RAW / "hms" / name,
                    force=force, expect_min_bytes=1024)

    reader = _open_bundle(path, year)
    rx0, ry0, rx1, ry1 = region_bbox()
    pts = [(loc.slug, loc.lon, loc.lat) for loc in LOCATIONS]

    hits: dict[tuple[str, dt.date], int] = {}
    dates_present: set[dt.date] = set()
    n_total = n_near = n_bad_geom = 0

    for sr in reader.iterShapeRecords():
        n_total += 1
        day = parse_hms_date(sr.record["Start"])
        if day is None:
            continue
        dates_present.add(day)

        bbox = sr.shape.bbox
        # Cheap reject: the vast majority of national plumes miss the Bay Area.
        if bbox[0] > rx1 or bbox[2] < rx0 or bbox[1] > ry1 or bbox[3] < ry0:
            continue
        n_near += 1

        try:
            geom = shapely_shape(sr.shape.__geo_interface__)
            if not geom.is_valid:
                # HMS polygons are hand-drawn and occasionally self-intersect.
                geom = geom.buffer(0)
        except Exception:  # noqa: BLE001 -- a broken polygon is not fatal
            n_bad_geom += 1
            continue

        rank = DENSITY_RANK.get(str(sr.record["Density"]).strip(), 1)
        for slug, lon, lat in pts:
            if shapely.contains_xy(geom, lon, lat):
                key = (slug, day)
                if rank > hits.get(key, 0):
                    hits[key] = rank

    log(f"  {year}: {n_total:,} polygons, {n_near:,} near the Bay Area, "
        f"{len(hits):,} location-days hit, {len(dates_present)} dates present"
        + (f", {n_bad_geom} unreadable geometries" if n_bad_geom else ""))
    return hits, dates_present


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--first-year", type=int, default=HMS_FIRST_YEAR)
    ap.add_argument("--last-year", type=int, default=dt.date.today().year)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--merge", action="store_true",
                    help="keep existing rows outside the scanned years, so the "
                         "monthly refresh can rescan only the current year "
                         "instead of re-downloading two decades of bundles")
    args = ap.parse_args(argv)

    log(f"[s03] NOAA HMS smoke polygons {args.first_year}-{args.last_year}")

    hits: dict[tuple[str, dt.date], int] = {}
    dates_present: set[dt.date] = set()
    for year in range(args.first_year, args.last_year + 1):
        try:
            h, d = scan_year(year, force=args.force)
        except RuntimeError as exc:
            log(f"  skip    {year}: {exc}")
            continue
        hits.update(h)
        dates_present |= d

    if not dates_present:
        raise SystemExit("s03: no HMS data retrieved at all -- aborting.")

    # HMS begins mid-2005; nothing before that can be classified at all.
    first = max(pd.Timestamp(HMS_FIRST_DATE), pd.Timestamp(min(dates_present)))
    last = pd.Timestamp(max(dates_present))
    all_days = pd.date_range(first, last, freq="D")

    rows = []
    for loc in LOCATIONS:
        for ts in all_days:
            day = ts.date()
            if day not in dates_present:
                # HMS published nothing anywhere -- unknown, not clean.
                rows.append((loc.slug, ts, float("nan"), None))
                continue
            rank = hits.get((loc.slug, day), 0)
            rows.append((
                loc.slug, ts, 1.0 if rank else 0.0, RANK_DENSITY.get(rank)
            ))

    out = pd.DataFrame(rows, columns=["location", "date", "plume", "density"])

    expected = len(LOCATIONS) * len(all_days)
    if len(out) != expected:
        raise SystemExit(f"s03: panel is {len(out)} rows, expected {expected}")

    if args.merge and OUT.exists():
        prior = pd.read_parquet(OUT)
        # Freshly scanned years win; everything older is carried forward.
        keep = prior[
            (prior["date"] < first) | (prior["date"] > last)
        ]
        out = (
            pd.concat([keep, out], ignore_index=True)
            .sort_values(["location", "date"])
            .reset_index(drop=True)
        )
        log(f"  merged with existing {OUT.name}: {len(keep):,} prior rows kept, "
            f"{len(out):,} total")
        first = out["date"].min()
        last = out["date"].max()
        all_days = pd.date_range(first, last, freq="D")

        if out.duplicated(subset=["location", "date"]).any():
            raise SystemExit("s03: merge produced duplicate location-days")
        if len(out) != len(LOCATIONS) * len(all_days):
            raise SystemExit("s03: merge left a gap in the location-day panel")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT, index=False)

    missing_days = len(all_days) - len(
        {d for d in dates_present if first.date() <= d <= last.date()}
    )
    smoky = out.groupby("location")["plume"].mean().sort_values()
    log(f"[s03] wrote {OUT.name}: {len(out):,} location-days, "
        f"{first.date()} -> {last.date()}, {missing_days} dates with no HMS file")
    log("  share of days with a plume overhead:")
    for slug, frac in smoky.items():
        log(f"    {slug:16s} {frac:6.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
