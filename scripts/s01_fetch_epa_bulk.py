#!/usr/bin/env python3
"""Stage 1: EPA AirData bulk daily PM2.5 for the Bay Area, 2000-2024.

Public, no key. Two parameter codes are needed and this is not optional:

  88101  PM2.5 FRM/FEM mass -- the main series
  88502  PM2.5 non-FRM     -- carries Bay Area continuous monitors through
                              roughly 2012, and Point Reyes to this day

Pulling only 88101 loses the region's best coastal background site entirely.

Bulk files stop being useful for the Bay Area after 2024 (BAAQMD certification
lag, issue #1) -- s02 picks up from there via the keyed API.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (  # noqa: E402
    BAY_AREA_COUNTIES,
    FIRST_YEAR,
    LOCATIONS,
    PARAM_FRM,
    PARAM_NONFRM,
    PROCESSED,
    RAW,
    STATE_CA,
)
from util import expect, log, read_zipped_csv, try_download  # noqa: E402

BASE = "https://aqs.epa.gov/aqsweb/airdata"
EPA_RAW = RAW / "epa"
OUT = PROCESSED / "epa_daily_bulk.parquet"

# The bulk files are only complete for the Bay Area through 2024; later years
# are handled by the API. We still scan them in case BAAQMD back-certifies.
LAST_BULK_YEAR = date.today().year

# One site emits many rows per day: Sample Duration x POC x Pollutant Standard
# x Event Type. Keep the 24-hour products only.
DURATIONS = {"24 HOUR", "24-HR BLK AVG"}

USECOLS = [
    "State Code", "County Code", "Site Num", "POC", "Latitude", "Longitude",
    "Sample Duration", "Date Local", "Observation Count", "Observation Percent",
    "Arithmetic Mean", "Local Site Name", "Event Type",
]

# site_id -> location slug, built from config so the two never drift apart.
SITE_TO_LOCATION = {
    m.site_id: loc.slug for loc in LOCATIONS for m in loc.monitors
}
MONITOR_BOUNDS = {
    m.site_id: (m.start, m.end) for loc in LOCATIONS for m in loc.monitors
}


def _load_year(param: str, year: int) -> pd.DataFrame:
    url = f"{BASE}/daily_{param}_{year}.zip"
    path = try_download(url, EPA_RAW / f"daily_{param}_{year}.zip")
    if path is None:
        return pd.DataFrame()

    df = read_zipped_csv(
        path,
        usecols=USECOLS,
        dtype={"State Code": "string", "County Code": "string", "Site Num": "string"},
    )
    df = df[
        (df["State Code"] == STATE_CA)
        & (df["County Code"].isin(BAY_AREA_COUNTIES))
        & (df["Sample Duration"].isin(DURATIONS))
    ]
    if df.empty:
        return df

    df["site_id"] = (
        df["State Code"] + "-" + df["County Code"] + "-" + df["Site Num"]
    )
    df = df[df["site_id"].isin(SITE_TO_LOCATION)]
    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["Date Local"])
    df["pm25"] = pd.to_numeric(df["Arithmetic Mean"], errors="coerce")
    df["param"] = param
    return df[["site_id", "date", "pm25", "param", "Observation Percent"]].rename(
        columns={"Observation Percent": "obs_pct"}
    )


def main() -> int:
    EPA_RAW.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)

    frames = []
    for year in range(FIRST_YEAR, LAST_BULK_YEAR + 1):
        for param in (PARAM_FRM, PARAM_NONFRM):
            df = _load_year(param, year)
            if not df.empty:
                frames.append(df)
                log(f"{year} {param}: {len(df):,} Bay Area site-day rows")

    expect(frames, "no EPA bulk data loaded at all -- check network access")
    raw = pd.concat(frames, ignore_index=True)

    # Average across POCs / standards within a site-day, then collapse the two
    # parameter codes preferring 88101 where a site reports both.
    per_param = (
        raw.groupby(["site_id", "date", "param"], as_index=False)
        .agg(pm25=("pm25", "mean"), obs_pct=("obs_pct", "max"))
    )
    per_param["pref"] = (per_param["param"] == PARAM_FRM).astype(int)
    per_param = (
        per_param.sort_values(["site_id", "date", "pref"], ascending=[True, True, False])
        .drop_duplicates(subset=["site_id", "date"], keep="first")
        .drop(columns=["pref"])
    )

    # Apply monitor validity windows so a replaced site does not contribute
    # outside its lifetime (Livermore, issue #4).
    keep = pd.Series(True, index=per_param.index)
    for site_id, (start, end) in MONITOR_BOUNDS.items():
        m = per_param["site_id"] == site_id
        if start:
            keep &= ~(m & (per_param["date"] < pd.Timestamp(start)))
        if end:
            keep &= ~(m & (per_param["date"] > pd.Timestamp(end)))
    dropped = int((~keep).sum())
    if dropped:
        log(f"dropped {dropped:,} rows outside monitor validity windows")
    per_param = per_param[keep]

    per_param["location"] = per_param["site_id"].map(SITE_TO_LOCATION)

    # Multiple monitors can serve one location (Oakland). Average them.
    out = (
        per_param.groupby(["location", "date"], as_index=False)
        .agg(
            pm25=("pm25", "mean"),
            obs_pct=("obs_pct", "max"),
            n_sites=("site_id", "nunique"),
        )
    )
    out["source"] = "epa_bulk"

    from util import check_no_duplicate_days, report_coverage

    check_no_duplicate_days(out)
    expect(
        (out["pm25"] < 2000).all(),
        "implausible PM2.5 above 2000 ug/m3 -- likely a unit or column mixup",
    )
    report_coverage(out, "EPA bulk")

    out.to_parquet(OUT, index=False)
    log(f"wrote {OUT.name}: {len(out):,} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
