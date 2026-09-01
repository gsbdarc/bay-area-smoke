"""Stage 02 -- EPA AQS API for the provisional tail, 2025 -> present.

The bulk AirData files stop being useful for the Bay Area after 2024: BAAQMD's
PM2.5 is still "preliminary pending data certification", so EPA's 2025 and 2026
bulk files contain only Santa Cruz county for our region (issue #1). The keyed
AQS API serves uncertified data and is the only way to reach the current fire
season.

Everything this stage produces is labelled **provisional** and stays visually
distinct in the UI. It is real measurement, but it has not cleared the agency
QA process and individual values can still change.

## Requires a key

Free, instant, emailed by `aqsdatamart@epa.gov`:

    curl "https://aqs.epa.gov/data/api/signup?email=YOU@example.edu"

Read from `AQS_EMAIL` / `AQS_KEY` in the environment or `.env`. A missing key is
a hard error, never a silent partial build -- a dataset that quietly stops in
2024 is far worse than one that refuses to build.

## Rate limits

EPA asks for at most 10 requests/minute and a pause between calls. We sleep
`PAUSE_SECONDS` between requests, which puts us comfortably under.

## Known upstream outage (2026-08-31)

At the time of writing every AQS *data* service returns HTTP 422 "No matching
service was found" -- including the literal example URLs in EPA's own
documentation, using EPA's own demo credentials. `metaData/isAvailable` reports
healthy, so this is a routing fault on their side, not a bad key.

That outage is why this stage distinguishes two failure modes rather than
treating everything as fatal:

- **no key** -> hard failure, because that is our fault and is fixable here.
- **service unreachable** -> loud warning, an empty result, and a recorded gap.
  The pipeline continues and the site renders with total PM2.5 ending in 2024,
  with the shortfall stated on the page instead of papered over.

`--require-api` turns the second case into a hard failure too, which is what
you want in CI once EPA is healthy again.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import BAY_AREA_COUNTIES, PARAM_FRM, PARAM_NONFRM, PROCESSED, STATE_CA  # noqa: E402
from fetch import USER_AGENT, load_env, log  # noqa: E402

API = "https://aqs.epa.gov/data/api"
OUT = PROCESSED / "epa_daily_api.parquet"
STATUS_OUT = PROCESSED / "epa_api_status.json"

# First year the bulk files stop covering the Bay Area. See LAST_CERTIFIED_YEAR
# in s01 -- these two must stay adjacent or a year falls through the gap.
FIRST_API_YEAR = 2025

PAUSE_SECONDS = 5.0
TIMEOUT = 120


class ServiceUnavailable(RuntimeError):
    """AQS is reachable but refusing to serve data (their side, not ours)."""


def get_credentials() -> tuple[str, str]:
    env = load_env()
    email, key = env.get("AQS_EMAIL"), env.get("AQS_KEY")
    if not email or not key:
        raise SystemExit(
            "s02: AQS_EMAIL and AQS_KEY are required but not set.\n"
            "  Get a free key:  curl \"https://aqs.epa.gov/data/api/signup"
            "?email=YOU@example.edu\"\n"
            "  Then put both in .env (gitignored), or export them.\n"
            "  In CI they come from GitHub Actions secrets.\n"
            "Refusing to build a dataset that silently stops in 2024."
        )
    return email, key


def _call(service: str, params: dict) -> list[dict]:
    r = requests.get(
        f"{API}/{service}", params=params, timeout=TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    )

    # AQS signals a bad service path with a bare JSON string and a 422.
    if r.status_code == 422 and r.text.lstrip().startswith('"'):
        raise ServiceUnavailable(f"{service}: {r.text.strip()[:160]}")
    r.raise_for_status()

    payload = r.json()
    if not isinstance(payload, dict):
        raise ServiceUnavailable(f"{service}: unexpected payload {payload!r:.160}")

    header = (payload.get("Header") or [{}])[0]
    status = str(header.get("status", ""))
    if status.lower().startswith("failed"):
        raise ServiceUnavailable(f"{service}: {status} {header.get('error')}")
    return payload.get("Data") or []


def fetch_county_year(email: str, key: str, county: str,
                      year: int) -> list[dict]:
    """Both PM2.5 parameter codes in one call.

    AQS accepts up to five comma-separated parameters per request, so asking
    for 88101 and 88502 together halves the number of rate-limited round trips
    -- and 88502 is not optional, it is the only place Point Reyes reports.
    """
    return _call("dailyData/byCounty", {
        "email": email, "key": key, "param": f"{PARAM_FRM},{PARAM_NONFRM}",
        "bdate": f"{year}0101", "edate": f"{year}1231",
        "state": STATE_CA, "county": county,
    })


def to_frame(records: list[dict]) -> pd.DataFrame:
    """Reshape AQS API records into the same shape s01 produces."""
    if not records:
        return pd.DataFrame(
            columns=["site_id", "date", "pm25", "param", "sample_duration",
                     "event_type", "lat", "lon", "site_name", "county_name"]
        )

    d = pd.DataFrame(records)
    d["site_id"] = (
        d["state_code"].astype(str).str.zfill(2) + "-"
        + d["county_code"].astype(str).str.zfill(3) + "-"
        + d["site_number"].astype(str).str.zfill(4)
    )
    d["date"] = pd.to_datetime(d["date_local"], errors="coerce")
    d["pm25"] = pd.to_numeric(d["arithmetic_mean"], errors="coerce")

    out = pd.DataFrame({
        "site_id": d["site_id"],
        "date": d["date"],
        "pm25": d["pm25"],
        "param": d.get("parameter_code", pd.Series(dtype=str)).astype(str),
        "sample_duration": d.get("sample_duration"),
        "event_type": d.get("event_type"),
        "lat": pd.to_numeric(d.get("latitude"), errors="coerce"),
        "lon": pd.to_numeric(d.get("longitude"), errors="coerce"),
        "site_name": d.get("local_site_name"),
        "county_name": d.get("county"),
    })
    out = out[out["date"].notna() & out["pm25"].notna()]

    # Same collapse discipline as s01: one value per site-day, FRM preferred.
    rank = {"24 HOUR": 0, "24-HR BLK AVG": 1, "1 HOUR": 2}
    out["dur_rank"] = out["sample_duration"].map(rank).fillna(3)
    out["param_rank"] = (out["param"] == PARAM_NONFRM).astype(int)
    out["event_rank"] = out["event_type"].map(
        {"Included": 0, "None": 1, "Excluded": 9}
    ).fillna(5)
    out = (
        out.sort_values(["site_id", "date", "dur_rank", "event_rank", "param_rank"])
        .drop_duplicates(subset=["site_id", "date"], keep="first")
        .drop(columns=["dur_rank", "param_rank", "event_rank"])
    )
    return out.reset_index(drop=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--first-year", type=int, default=FIRST_API_YEAR)
    ap.add_argument("--last-year", type=int, default=dt.date.today().year)
    ap.add_argument("--require-api", action="store_true",
                    help="treat an AQS outage as fatal (use in CI)")
    args = ap.parse_args(argv)

    email, key = get_credentials()
    log(f"[s02] EPA AQS API {args.first_year}-{args.last_year} "
        f"({len(BAY_AREA_COUNTIES)} counties, provisional data)")

    records: list[dict] = []
    errors: list[str] = []
    n_calls = 0

    aborted = False
    for year in range(args.first_year, args.last_year + 1):
        if aborted:
            break
        for county, cname in BAY_AREA_COUNTIES.items():
            if n_calls:
                time.sleep(PAUSE_SECONDS)
            n_calls += 1
            try:
                got = fetch_county_year(email, key, county, year)
            except ServiceUnavailable as exc:
                errors.append(str(exc))
                log(f"  {year} {cname}: UNAVAILABLE")
                # One routing fault means they are all broken; do not spend
                # twenty rate-limited requests proving it.
                if len(errors) >= 3:
                    log("  [s02] AQS is refusing every data service; "
                        "stopping early.")
                    aborted = True
                    break
                continue
            except requests.RequestException as exc:
                errors.append(f"{year} {cname}: {exc}")
                log(f"  {year} {cname}: {exc}")
                continue

            if got:
                log(f"  {year} {cname}: {len(got)} rows")
            records.extend(got)

    df = to_frame(records)
    df["provenance"] = "provisional"

    status = {
        "ok": bool(len(df)),
        "rows": int(len(df)),
        "calls": n_calls,
        "first_year": args.first_year,
        "last_year": args.last_year,
        "checked_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "errors": errors[:5],
        "note": (
            "EPA AQS data services returned no data. The site's total-PM2.5 "
            "series therefore ends with the last certified bulk year."
            if not len(df) else ""
        ),
    }
    STATUS_OUT.parent.mkdir(parents=True, exist_ok=True)
    STATUS_OUT.write_text(json.dumps(status, indent=2) + "\n")

    if not len(df):
        msg = (
            "s02: AQS returned no data.\n"
            + ("  " + "\n  ".join(errors[:3]) if errors else "")
            + "\n  Total PM2.5 will end at the last certified bulk year, and "
              "the site will say so."
        )
        if args.require_api:
            raise SystemExit(msg.replace("s02:", "s02 (--require-api):"))
        log(msg)
        # An empty, well-formed file is what downstream stages expect.
        df.to_parquet(OUT, index=False)
        return 0

    df.to_parquet(OUT, index=False)
    log(f"[s02] wrote {OUT.name}: {len(df):,} site-days, "
        f"{df['date'].min().date()} -> {df['date'].max().date()} (provisional)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
