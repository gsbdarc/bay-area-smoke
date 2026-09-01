"""Stage 01 -- EPA AirData bulk daily PM2.5 for the Bay Area, 2000 -> present.

Downloads `daily_88101_YYYY.zip` and `daily_88502_YYYY.zip`, keeps only the ten
Bay Area counties, collapses EPA's many-rows-per-site-day encoding down to one
value per site-day, and writes a tidy parquet.

Why both parameter codes: 88101 is FRM/FEM PM2.5 and 88502 is non-FRM. The Bay
Area's continuous monitors reported under 88502 until roughly 2012-2015, and
**Point Reyes still does** -- our single best coastal background site is
invisible if you only pull 88101.

## The many-rows-per-site-day problem

One site emits several rows for the same date, multiplied across `POC`
(instrument number), `Sample Duration`, `Pollutant Standard`, and `Event Type`.
Loading naively double- or triple-counts days. We rank the candidates and keep
exactly one.

Two places where the ranking is doing real work, both found by inspecting the
live files rather than by reading the docs:

- **`Pollutant Standard` is NULL for 88502.** Filtering to a single named
  standard -- the obvious move -- silently drops every non-FRM row, i.e. all of
  Point Reyes. So we rank rather than filter.
- **`1 HOUR` rows are a needed fallback, not noise.** They are the daily mean of
  hourly samples. Usually they duplicate a 24-hour row, but in 2020 alone 84
  Bay Area site-days exist *only* in `1 HOUR` form (Livermore, Oakland, Napa
  among them). We take them when nothing better exists, and only if at least 18
  of 24 hours were observed -- EPA's own 75% completeness rule for calling a
  daily average valid.

- **`Event Type == 'Excluded'` is actively wrong for this project.** Those rows
  have exceptional-event data -- which is to say *wildfire smoke* -- removed.
  Ranking them last means we use the smoke-inclusive value wherever EPA
  published one.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import BAY_AREA_COUNTIES, FIRST_YEAR, PARAM_FRM, PARAM_NONFRM, PROCESSED, RAW, STATE_CA  # noqa: E402
from fetch import download, log  # noqa: E402

BASE = "https://aqs.epa.gov/aqsweb/airdata"
OUT = PROCESSED / "epa_daily_bulk.parquet"

# Last year EPA considers agency-certified for our region. Rows after this are
# real measurements but have not cleared certification, so they are labelled
# provisional in the UI rather than silently mixed in. See PLAN.md finding 3.
LAST_CERTIFIED_YEAR = 2024

# Lower is better. Reference-method filter samples first, continuous block
# averages next, hourly-derived daily means only as a fallback.
DURATION_RANK = {"24 HOUR": 0, "24-HR BLK AVG": 1, "1 HOUR": 2}

# Lower is better. 'Excluded' has had wildfire data stripped out -- last resort.
EVENT_RANK = {"Included": 0, "None": 1, "Excluded": 9}

# EPA's completeness rule: a daily average needs 75% of hours.
MIN_HOURS = 18

USECOLS = [
    "State Code", "County Code", "Site Num", "POC", "Latitude", "Longitude",
    "Sample Duration", "Pollutant Standard", "Date Local", "Event Type",
    "Observation Count", "Observation Percent", "Arithmetic Mean",
    "Local Site Name", "County Name",
]


def _year_urls(year: int) -> list[tuple[str, str, Path]]:
    out = []
    for param in (PARAM_FRM, PARAM_NONFRM):
        name = f"daily_{param}_{year}.zip"
        out.append((param, f"{BASE}/{name}", RAW / "epa" / name))
    return out


def load_year(year: int, *, force: bool = False) -> pd.DataFrame:
    """Download and reduce one year to one row per (site, date)."""
    frames = []
    for param, url, dest in _year_urls(year):
        try:
            path = download(url, dest, force=force, expect_min_bytes=1024)
        except RuntimeError as exc:
            # 88502 does not exist for every year, and the current year may not
            # be published yet. A missing file is information, not a failure.
            log(f"  skip    {dest.name}: {exc}")
            continue

        df = pd.read_csv(path, dtype=str, usecols=USECOLS)
        df = df[
            (df["State Code"] == STATE_CA)
            & (df["County Code"].isin(BAY_AREA_COUNTIES))
        ].copy()
        if df.empty:
            log(f"  {dest.name}: no Bay Area rows")
            continue
        df["param"] = param
        frames.append(df)

    if not frames:
        return pd.DataFrame()

    raw = pd.concat(frames, ignore_index=True)
    return _collapse(raw, year)


def _collapse(raw: pd.DataFrame, year: int) -> pd.DataFrame:
    """One row per (site, date), chosen by the documented ranking."""
    d = raw.copy()
    d["site_id"] = (
        d["State Code"] + "-" + d["County Code"] + "-" + d["Site Num"]
    )
    d["date"] = pd.to_datetime(d["Date Local"], errors="coerce")
    d["pm25"] = pd.to_numeric(d["Arithmetic Mean"], errors="coerce")
    d["obs"] = pd.to_numeric(d["Observation Count"], errors="coerce")
    d["poc"] = pd.to_numeric(d["POC"], errors="coerce").fillna(99)
    d["lat"] = pd.to_numeric(d["Latitude"], errors="coerce")
    d["lon"] = pd.to_numeric(d["Longitude"], errors="coerce")

    before = len(d)
    d = d[d["date"].notna() & d["pm25"].notna()]

    # An hourly-derived daily mean is only usable if the day was mostly
    # observed. 24-hour rows carry Observation Count 1 and are exempt.
    hourly = d["Sample Duration"] == "1 HOUR"
    d = d[~hourly | (d["obs"] >= MIN_HOURS)]

    d["dur_rank"] = d["Sample Duration"].map(DURATION_RANK)
    d = d[d["dur_rank"].notna()]
    # 88101 (FRM/FEM) beats 88502 (non-FRM) when a site reports both.
    d["param_rank"] = (d["param"] == PARAM_NONFRM).astype(int)
    d["event_rank"] = d["Event Type"].map(EVENT_RANK).fillna(5)

    d = d.sort_values(
        ["site_id", "date", "dur_rank", "event_rank", "param_rank", "poc"]
    )
    out = d.drop_duplicates(subset=["site_id", "date"], keep="first")

    log(
        f"  {year}: {before} raw rows -> {len(out)} site-days "
        f"({out['site_id'].nunique()} sites)"
    )

    out = out[[
        "site_id", "date", "pm25", "param", "Sample Duration", "Event Type",
        "lat", "lon", "Local Site Name", "County Name",
    ]].rename(columns={
        "Sample Duration": "sample_duration",
        "Event Type": "event_type",
        "Local Site Name": "site_name",
        "County Name": "county_name",
    })
    out["year"] = year
    return out.reset_index(drop=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--first-year", type=int, default=FIRST_YEAR)
    ap.add_argument("--last-year", type=int, default=dt.date.today().year)
    ap.add_argument("--force", action="store_true",
                    help="re-download even if the zip is cached")
    args = ap.parse_args(argv)

    log(f"[s01] EPA bulk AirData {args.first_year}-{args.last_year}")
    frames = []
    for year in range(args.first_year, args.last_year + 1):
        df = load_year(year, force=args.force)
        if not df.empty:
            frames.append(df)

    if not frames:
        raise SystemExit("s01: no EPA bulk data retrieved at all -- aborting.")

    all_df = pd.concat(frames, ignore_index=True)

    # Provenance travels with the row from here on. Everything downstream, and
    # ultimately the UI, distinguishes certified from provisional.
    all_df["provenance"] = [
        "certified" if y <= LAST_CERTIFIED_YEAR else "provisional"
        for y in all_df["year"]
    ]

    dupes = all_df.duplicated(subset=["site_id", "date"]).sum()
    if dupes:
        raise SystemExit(f"s01: {dupes} duplicate site-days survived collapse")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    all_df.to_parquet(OUT, index=False)

    log(f"[s01] wrote {OUT.relative_to(PROCESSED.parent.parent)} "
        f"({len(all_df):,} site-days, "
        f"{all_df['date'].min().date()} -> {all_df['date'].max().date()})")
    by = all_df.groupby("provenance")["date"].agg(["count", "min", "max"])
    log(by.to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
