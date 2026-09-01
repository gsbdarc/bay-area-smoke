"""Stage 02b -- AirNow daily files, to fill the 2025+ hole AQS leaves.

BAAQMD's PM2.5 reaches EPA's *archive* (AQS) slowly and site by site, so both
the bulk files and the keyed API had nothing for most of the Bay Area in 2025
(issues #1, #7). But the same agency pushes the same monitors' readings to
**AirNow** in real time, and AirNow's daily files are public, keyless, and
complete across exactly the window AQS is missing.

This is not modelled or estimated data. It is the same instruments, taken from
the real-time channel instead of the archive -- which is precisely why it is
available sooner and why it carries less QA. Everything here is labelled
`provisional-airnow` and ranks below both certified bulk and the AQS API, so it
only ever fills a hole, never overwrites a better number.

## The files

    https://files.airnowtech.org/airnow/YYYY/YYYYMMDD/daily_data_v2.dat

Pipe-delimited, one row per site per parameter, no header:

    date | site_id | name | param | unit | value | avg_hours | agency |
    aqi | category | lat | lon | full_id

`site_id` is `SSCCCNNNN` -- the same state/county/site as our hyphenated ids,
just unpunctuated. We keep `PM2.5-24hr` rows only: those are the completed
24-hour averages, which is what the rest of the pipeline is built on.

One file per day and no bulk rollup, so a full backfill is ~600 requests. They
are cached on scratch and the fetch is incremental, so the monthly refresh
pulls only the handful of days that are new.

## What it does and does not reach

Six of our ten locations: San Francisco, Oakland, Redwood City, San Jose,
Sebastopol, Santa Cruz. Point Reyes is a National Park Service site and is not
in AirNow, but it is still in the AQS bulk files, so it is covered anyway.

Livermore is absent from AirNow entirely. Napa and Half Moon Bay have no
monitor to report -- no channel can conjure data that was never measured.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import LOCATIONS, PROCESSED, RAW  # noqa: E402
from fetch import download, log  # noqa: E402

BASE = "https://files.airnowtech.org/airnow"
OUT = PROCESSED / "airnow_daily.parquet"

# AQS bulk is complete through 2024, so there is nothing to fill before this.
FIRST_DATE = dt.date(2025, 1, 1)

# The completed 24-hour average. AirNow also carries shorter aggregates that
# would not be comparable with the rest of the record.
PARAM = "PM2.5-24hr"

COLUMNS = [
    "date", "site_raw", "site_name", "param", "unit", "value", "avg_hours",
    "agency", "aqi", "category", "lat", "lon", "full_id",
]

# AirNow's missing-data sentinel. Small negatives are real instrument noise
# near zero and are kept (aqi.py clamps them); -999 is "no value".
MISSING = -900.0

# Only the sites our locations actually use, keyed the way AirNow writes them.
WANTED = {
    m.site_id.replace("-", ""): (loc.slug, m.site_id)
    for loc in LOCATIONS for m in loc.monitors
}


def day_url(d: dt.date) -> str:
    return f"{BASE}/{d:%Y}/{d:%Y%m%d}/daily_data_v2.dat"


def parse_day(path: Path, d: dt.date) -> list[dict]:
    """Pull our sites' PM2.5 out of one daily file."""
    rows = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("|")
            if len(parts) < 13 or parts[3] != PARAM:
                continue
            site = parts[1]
            if site not in WANTED:
                continue
            try:
                value = float(parts[5])
            except ValueError:
                continue
            if value <= MISSING:
                continue
            slug, site_id = WANTED[site]
            rows.append({
                "site_id": site_id,
                "location": slug,
                "date": pd.Timestamp(d),
                "pm25": value,
                "site_name": parts[2],
                "agency": parts[7],
            })
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--first-date", type=str, default=str(FIRST_DATE))
    ap.add_argument("--last-date", type=str,
                    default=str(dt.date.today() - dt.timedelta(days=1)))
    ap.add_argument("--merge", action="store_true",
                    help="keep previously fetched days and add only new ones")
    args = ap.parse_args(argv)

    first = dt.date.fromisoformat(args.first_date)
    last = dt.date.fromisoformat(args.last_date)

    have: set[pd.Timestamp] = set()
    prior = pd.DataFrame()
    if args.merge and OUT.exists():
        prior = pd.read_parquet(OUT)
        have = set(prior["date"].unique())
        log(f"[s02b] {len(prior):,} rows already on disk; fetching only new days")

    days = [first + dt.timedelta(days=i) for i in range((last - first).days + 1)]
    todo = [d for d in days if pd.Timestamp(d) not in have]
    log(f"[s02b] AirNow daily files {first} -> {last} "
        f"({len(todo)} of {len(days)} days to fetch)")

    rows: list[dict] = []
    n_missing = 0
    for i, d in enumerate(todo, 1):
        dest = RAW / "airnow" / f"{d:%Y}" / f"daily_data_v2_{d:%Y%m%d}.dat"
        try:
            path = download(day_url(d), dest, expect_min_bytes=1024, timeout=90)
        except RuntimeError:
            # A missing day is normal at the very end of the range and around
            # AirNow outages. It is a gap, not a failure.
            n_missing += 1
            continue
        rows.extend(parse_day(path, d))
        if i % 60 == 0:
            log(f"  ... {i}/{len(todo)} days, {len(rows):,} rows so far")

    fresh = pd.DataFrame(rows)
    if len(prior) and len(fresh):
        out = pd.concat([prior, fresh], ignore_index=True)
    elif len(prior):
        out = prior
    else:
        out = fresh

    if out.empty:
        log("[s02b] no AirNow rows retrieved; writing an empty file")
        out = pd.DataFrame(columns=["site_id", "location", "date", "pm25",
                                    "site_name", "agency", "provenance"])
        OUT.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(OUT, index=False)
        return 0

    out = (
        out.sort_values(["site_id", "date"])
        .drop_duplicates(subset=["site_id", "date"], keep="last")
        .reset_index(drop=True)
    )
    out["provenance"] = "provisional-airnow"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT, index=False)

    log(f"[s02b] wrote {OUT.name}: {len(out):,} site-days, "
        f"{out['date'].min().date()} -> {out['date'].max().date()}"
        + (f", {n_missing} days unavailable" if n_missing else ""))
    by = out.groupby("location")["date"].agg(["count", "min", "max"])
    log("  coverage added:\n" + by.to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
