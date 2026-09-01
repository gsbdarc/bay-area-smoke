"""Stage 05 -- turn the panel into the JSON the site loads.

Three outputs:

  site/data/locations.json      metadata, grid cells, monitors, caveats
  site/data/daily/<slug>.json   the daily series, columnar
  site/data/climatology.json    day-of-year statistics -- the headline view

## Columnar, and dates by construction

Row objects (`[{"date":..., "pm25":...}, ...]`) repeat every key on every row.
Columnar (`{"pm25": [...], ...}`) does not, which is 3-5x smaller and parses
faster. And because the panel is complete -- every location has every day -- we
publish `start` plus parallel arrays and let the browser derive dates by index,
instead of shipping 9,700 date strings per location.

Values are rounded on the way out (PM2.5 to 0.1 ug/m3, AQI to integer). The
precision beyond that is not real, and carrying it inflates the payload.

## The climatology, and the denominator that matters

For each location and each calendar date we answer: *how often is this day
smoky?* Estimated by pooling a +/-7-day window across all years, so a single
freak September 14 does not dominate the value for September 14.

The denominator is the part that is easy to get wrong and impossible to spot
afterwards. A year only counts toward a location's statistic if that location
actually has data that year -- `MIN_WINDOW_COVERAGE` of the window days must be
non-missing. Without that rule Napa, which lost its monitor in 2021, would
divide its handful of smoky days by two decades of "years" and come out looking
like the safest place in the region. Missing data must never read as clean air.

Two thresholds, matching the UI toggle:
  * AQI >= 101  -- Unhealthy for Sensitive Groups, the "don't hold it
    outdoors" line
  * smoke PM2.5 >= 5 ug/m3 -- noticeable wildfire smoke
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aqi import USG_THRESHOLD  # noqa: E402
from config import BY_SLUG, LOCATIONS, PROCESSED, SITE_DATA  # noqa: E402
from fetch import human, log  # noqa: E402
from smoke import NOTICEABLE_SMOKE  # noqa: E402

PANEL = PROCESSED / "daily_panel.parquet"
CELLS = PROCESSED / "grid_cells.csv"
XVAL = PROCESSED / "crossval.json"
API_STATUS = PROCESSED / "epa_api_status.json"

WINDOW_DAYS = 7          # +/- this many days around each calendar date
MIN_WINDOW_COVERAGE = 0.6  # a year must have this share of window days to count

# Compact provenance codes; the legend travels with locations.json so the
# browser never has to hard-code these.
PM25_SOURCE_CODES = {"none": 0, "certified": 1, "provisional": 2}
SMOKE_SOURCE_CODES = {"none": 0, "echolab-v1": 1, "echolab-v2": 1, "ours": 2}
SOURCE_LEGEND = {
    "pm25": {"0": "no data", "1": "certified measurement",
             "2": "provisional measurement"},
    "smoke": {"0": "no data", "1": "published model (ECHOLab)",
              "2": "our extension of their method"},
}

# A reference leap year, so every (month, day) including Feb 29 gets a slot.
REF_YEAR = 2020
N_SLOTS = 366


def slot_of(month: int, day: int) -> int:
    return (dt.date(REF_YEAR, month, day) - dt.date(REF_YEAR, 1, 1)).days


def slot_label(slot: int) -> str:
    d = dt.date(REF_YEAR, 1, 1) + dt.timedelta(days=slot)
    return f"{d.month:02d}-{d.day:02d}"


def _round(series: pd.Series, digits: int):
    """Round, then emit as JSON with NaN as null rather than the invalid NaN."""
    vals = series.round(digits)
    return [None if pd.isna(v) else (int(v) if digits == 0 else float(v))
            for v in vals]


def write_daily(panel: pd.DataFrame, outdir: Path) -> dict[str, int]:
    outdir.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for slug, g in panel.groupby("location"):
        g = g.sort_values("date")
        days = pd.date_range(g["date"].min(), g["date"].max(), freq="D")
        if len(g) != len(days):
            raise SystemExit(
                f"s05: {slug} has {len(g)} rows for {len(days)} days -- the "
                "panel must be gap-free before it is published."
            )
        doc = {
            "location": slug,
            "start": g["date"].min().strftime("%Y-%m-%d"),
            "n": int(len(g)),
            "pm25": _round(g["pm25"], 1),
            "aqi": _round(g["aqi"], 0),
            "smoke_pm": _round(g["smoke_pm"], 1),
            "plume": [None if pd.isna(v) else int(v) for v in g["plume"]],
            "pm25_src": [PM25_SOURCE_CODES.get(s, 0) for s in g["pm25_source"]],
            "smoke_src": [SMOKE_SOURCE_CODES.get(s, 0) for s in g["smoke_source"]],
        }
        path = outdir / f"{slug}.json"
        path.write_text(json.dumps(doc, separators=(",", ":")))
        sizes[slug] = path.stat().st_size
    return sizes


def climatology(panel: pd.DataFrame) -> dict:
    """Day-of-year exceedance rates, medians, and worst years."""
    d = panel.copy()
    d["year"] = d["date"].dt.year
    d["slot"] = [
        slot_of(ts.month, ts.day) for ts in d["date"]
    ]
    d["hit_aqi"] = d["aqi"] >= USG_THRESHOLD
    d["hit_smoke"] = d["smoke_pm"] >= NOTICEABLE_SMOKE

    # Precompute the +/-7 day neighbourhood of every slot, with wraparound so
    # late December and early January are neighbours.
    neighbours = {
        s: [(s + k) % N_SLOTS for k in range(-WINDOW_DAYS, WINDOW_DAYS + 1)]
        for s in range(N_SLOTS)
    }
    window_size = 2 * WINDOW_DAYS + 1

    out: dict[str, dict] = {}
    for slug, g in d.groupby("location"):
        by_slot = {s: sub for s, sub in g.groupby("slot")}

        p_aqi, p_smoke = [], []
        pm_med, pm_p90, sm_p90 = [], [], []
        n_obs_aqi, n_obs_smoke = [], []
        n_years_aqi, n_years_smoke = [], []
        worst_year, worst_val = [], []
        years_hit_aqi, years_hit_smoke = [], []

        for s in range(N_SLOTS):
            parts = [by_slot[n] for n in neighbours[s] if n in by_slot]
            win = pd.concat(parts) if parts else g.iloc[0:0]

            # Which years have enough of this window to be counted at all?
            per_year = win.groupby("year")
            cov_aqi = per_year["aqi"].apply(lambda x: x.notna().sum() / window_size)
            cov_smk = per_year["smoke_pm"].apply(lambda x: x.notna().sum() / window_size)
            ok_aqi = set(cov_aqi[cov_aqi >= MIN_WINDOW_COVERAGE].index)
            ok_smk = set(cov_smk[cov_smk >= MIN_WINDOW_COVERAGE].index)

            wa = win[win["year"].isin(ok_aqi) & win["aqi"].notna()]
            ws = win[win["year"].isin(ok_smk) & win["smoke_pm"].notna()]

            p_aqi.append(round(float(wa["hit_aqi"].mean()), 4) if len(wa) else None)
            p_smoke.append(round(float(ws["hit_smoke"].mean()), 4) if len(ws) else None)
            pm_med.append(round(float(wa["pm25"].median()), 1) if len(wa) else None)
            pm_p90.append(round(float(wa["pm25"].quantile(0.9)), 1) if len(wa) else None)
            sm_p90.append(round(float(ws["smoke_pm"].quantile(0.9)), 1) if len(ws) else None)

            # Two different statistics, and they must not be mixed up.
            #
            # `p_*` above is a PER-DAY rate: exceedances divided by
            # day-observations (years x ~15 window days). It answers "if I book
            # this date, how likely is it to be bad?"
            #
            # `years_hit_*` is a PER-YEAR count: in how many years did at least
            # one day in the window exceed. It is always the larger-looking
            # number, because bad days cluster into episodes -- San Jose around
            # 22 November is 7% of days but 39% of years.
            #
            # Each needs ITS OWN denominator. A year can qualify for AQI and not
            # for smoke, so a single shared `n_years` silently paired an AQI
            # numerator with a smoke denominator: 9 of 23 was published as
            # "9 of 20".
            n_obs_aqi.append(int(len(wa)))
            n_obs_smoke.append(int(len(ws)))
            n_years_aqi.append(len(ok_aqi))
            n_years_smoke.append(len(ok_smk))
            years_hit_aqi.append(
                int(wa.groupby("year")["hit_aqi"].any().sum()) if len(wa) else 0
            )
            years_hit_smoke.append(
                int(ws.groupby("year")["hit_smoke"].any().sum()) if len(ws) else 0
            )

            if len(wa) and wa["pm25"].notna().any():
                i = wa["pm25"].idxmax()
                worst_year.append(int(wa.loc[i, "year"]))
                worst_val.append(round(float(wa.loc[i, "pm25"]), 1))
            else:
                worst_year.append(None)
                worst_val.append(None)

        out[slug] = {
            "p_aqi": p_aqi,
            "p_smoke": p_smoke,
            "pm25_median": pm_med,
            "pm25_p90": pm_p90,
            "smoke_p90": sm_p90,
            "n_years_aqi": n_years_aqi,
            "n_years_smoke": n_years_smoke,
            "n_obs_aqi": n_obs_aqi,
            "n_obs_smoke": n_obs_smoke,
            "years_hit_aqi": years_hit_aqi,
            "years_hit_smoke": years_hit_smoke,
            "worst_year": worst_year,
            "worst_pm25": worst_val,
        }
    return out


def build_locations(panel: pd.DataFrame) -> dict:
    cells = (
        pd.read_csv(CELLS).set_index("slug").to_dict("index")
        if CELLS.exists() else {}
    )
    xval = json.loads(XVAL.read_text()) if XVAL.exists() else {}
    api_status = json.loads(API_STATUS.read_text()) if API_STATUS.exists() else {}

    entries = []
    for loc in LOCATIONS:
        g = panel[panel["location"] == loc.slug]
        meas = g[g["pm25"].notna()]
        smoke = g[g["smoke_pm"].notna()]
        cell = cells.get(loc.slug, {})
        entries.append({
            "slug": loc.slug,
            "name": loc.name,
            "lat": loc.lat,
            "lon": loc.lon,
            "blurb": loc.blurb,
            "coverage_note": loc.coverage_note,
            "grid_id_10km": cell.get("grid_id_10km"),
            "grid_match": cell.get("match"),
            "monitors": [
                {"site_id": m.site_id, "label": m.label,
                 "start": m.start, "end": m.end}
                for m in loc.monitors
            ],
            "pm25_first": meas["date"].min().strftime("%Y-%m-%d") if len(meas) else None,
            "pm25_last": meas["date"].max().strftime("%Y-%m-%d") if len(meas) else None,
            "pm25_days": int(len(meas)),
            "smoke_first": smoke["date"].min().strftime("%Y-%m-%d") if len(smoke) else None,
            "smoke_last": smoke["date"].max().strftime("%Y-%m-%d") if len(smoke) else None,
        })

    return {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "start": panel["date"].min().strftime("%Y-%m-%d"),
        "end": panel["date"].max().strftime("%Y-%m-%d"),
        "thresholds": {"aqi": USG_THRESHOLD, "smoke_pm": NOTICEABLE_SMOKE},
        "window_days": WINDOW_DAYS,
        "min_window_coverage": MIN_WINDOW_COVERAGE,
        "source_legend": SOURCE_LEGEND,
        "slot_labels": [slot_label(s) for s in range(N_SLOTS)],
        "locations": entries,
        "crossval": xval,
        "api_status": api_status,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args(argv)

    if not PANEL.exists():
        raise SystemExit("s05: daily_panel.parquet missing -- run s04 first.")
    panel = pd.read_parquet(PANEL)

    SITE_DATA.mkdir(parents=True, exist_ok=True)
    log("[s05] writing daily series")
    sizes = write_daily(panel, SITE_DATA / "daily")

    log("[s05] computing day-of-year climatology")
    clim = climatology(panel)
    clim_doc = {"window_days": WINDOW_DAYS, "by_location": clim}
    (SITE_DATA / "climatology.json").write_text(
        json.dumps(clim_doc, separators=(",", ":"))
    )

    meta = build_locations(panel)
    (SITE_DATA / "locations.json").write_text(json.dumps(meta, indent=1))

    # Nothing with a NaN may reach the browser -- JSON has no NaN literal and
    # JSON.parse would throw on the whole file.
    for path in sorted(SITE_DATA.rglob("*.json")):
        text = path.read_text()
        if "NaN" in text or "Infinity" in text:
            raise SystemExit(f"s05: {path.name} contains a non-JSON number")

    total = sum(p.stat().st_size for p in SITE_DATA.rglob("*.json"))
    log(f"[s05] wrote {len(sizes)} daily files + climatology + locations; "
        f"{human(total)} total uncompressed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
