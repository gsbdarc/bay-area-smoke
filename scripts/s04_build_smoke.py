"""Stage 04 -- assemble the location x day panel and attribute smoke.

Joins everything upstream into one table, one row per location per day from
2000-01-01 to the last day we have any data for:

    location, date, pm25, aqi, plume, density, smoke_pm, smoke_source,
    pm25_source, n_monitors

## Stitching monitors into locations

`config.py` gives each location its monitors and, where relevant, the date
window in which each is the valid source. Two of those windows exist because
the network changed under us and the change looks exactly like signal:

- **Livermore** swapped `06-001-0007` for `06-001-0016` on 2024-02-04. Without
  the windows the inland East Bay line simply stops in early 2024.
- **Napa** ran `06-055-0004` only from 2018 to 2021-05-20. Outside that window
  Napa has no EPA measurement at all, and the panel must say NaN rather than
  imply clean air.

Where a location has two concurrently valid monitors (Oakland), we average
them and record `n_monitors`.

## Where each smoke number comes from

`smoke_pm` is assembled from two sources and the seam is never blended:

| Period | Source | `smoke_source` |
|---|---|---|
| 2006-01-01 .. 2023-12-31 | ECHOLab published 10 km grid | `echolab-v2` |
| 2024-01-01 .. present | our Childs-method reimplementation | `ours` |

We also run our own method across the *published* years, which costs nothing
extra and buys a real check: `--verify` reports the correlation between the two
on their overlap. If our reimplementation disagrees badly with the published
product on the same days, that is a finding to log, not something to bury.

## Why our extension has holes

Our method needs a measured PM2.5 anomaly, so it can only extend locations that
still have a working monitor. Half Moon Bay never had one and Napa lost its in
2021, so both stop at 2023 when ECHOLab stops. That is a real gap, it is
recorded in `pm25_source`, and s05 refuses to count those years in the
climatology denominator rather than reading them as clean.
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

from aqi import pm25_to_aqi  # noqa: E402
from config import FIRST_YEAR, LOCATIONS, PROCESSED  # noqa: E402
from fetch import log  # noqa: E402
from smoke import attribute_smoke  # noqa: E402

OUT = PROCESSED / "daily_panel.parquet"
XVAL_OUT = PROCESSED / "crossval.json"

BULK = PROCESSED / "epa_daily_bulk.parquet"
API = PROCESSED / "epa_daily_api.parquet"
HMS = PROCESSED / "smoke_days.parquet"
ECHOLAB = PROCESSED / "echolab_smokepm.parquet"


def load_measurements() -> pd.DataFrame:
    """Bulk + API site-days, deduped with certified data winning."""
    frames = []
    if BULK.exists():
        frames.append(pd.read_parquet(BULK))
    else:
        raise SystemExit("s04: epa_daily_bulk.parquet missing -- run s01 first.")
    if API.exists():
        api = pd.read_parquet(API)
        if len(api):
            frames.append(api)

    cols = ["site_id", "date", "pm25", "provenance"]
    d = pd.concat([f[cols] for f in frames], ignore_index=True)

    # A site-day present in both the certified bulk file and the provisional
    # API feed should read as certified.
    d["rank"] = (d["provenance"] != "certified").astype(int)
    d = (
        d.sort_values(["site_id", "date", "rank"])
        .drop_duplicates(subset=["site_id", "date"], keep="first")
        .drop(columns=["rank"])
    )
    return d


def site_to_location(meas: pd.DataFrame) -> pd.DataFrame:
    """Attach a location slug, honouring each monitor's validity window."""
    pieces = []
    for loc in LOCATIONS:
        for m in loc.monitors:
            sel = meas["site_id"] == m.site_id
            if m.start:
                sel &= meas["date"] >= pd.Timestamp(m.start)
            if m.end:
                sel &= meas["date"] <= pd.Timestamp(m.end)
            part = meas[sel].copy()
            if part.empty:
                log(f"  note: {loc.slug}/{m.site_id} contributed no rows")
                continue
            part["location"] = loc.slug
            pieces.append(part)

    if not pieces:
        raise SystemExit("s04: no EPA measurement mapped to any location.")
    return pd.concat(pieces, ignore_index=True)


def collapse_to_location(mapped: pd.DataFrame) -> pd.DataFrame:
    """Average concurrently valid monitors into one value per location-day."""
    g = mapped.groupby(["location", "date"])
    out = g.agg(
        pm25=("pm25", "mean"),
        n_monitors=("site_id", "nunique"),
    ).reset_index()
    # If any contributing row was certified, call the day certified.
    prov = (
        mapped.assign(cert=mapped["provenance"] == "certified")
        .groupby(["location", "date"])["cert"].any()
        .map({True: "certified", False: "provisional"})
        .rename("pm25_source")
        .reset_index()
    )
    return out.merge(prov, on=["location", "date"], how="left")


def build_panel() -> pd.DataFrame:
    meas = load_measurements()
    mapped = site_to_location(meas)
    per_loc = collapse_to_location(mapped)

    hms = pd.read_parquet(HMS) if HMS.exists() else pd.DataFrame()
    if hms.empty:
        raise SystemExit("s04: smoke_days.parquet missing -- run s03 first.")

    last = max(per_loc["date"].max(), hms["date"].max())
    days = pd.date_range(f"{FIRST_YEAR}-01-01", last, freq="D")
    panel = pd.MultiIndex.from_product(
        [[loc.slug for loc in LOCATIONS], days], names=["location", "date"]
    ).to_frame(index=False)

    panel = panel.merge(per_loc, on=["location", "date"], how="left")
    panel = panel.merge(hms, on=["location", "date"], how="left")

    panel["n_monitors"] = panel["n_monitors"].fillna(0).astype(int)
    panel["pm25_source"] = panel["pm25_source"].fillna("none")
    panel["aqi"] = pm25_to_aqi(panel["pm25"].to_numpy())

    expected = len(LOCATIONS) * len(days)
    if len(panel) != expected:
        raise SystemExit(f"s04: panel is {len(panel)} rows, expected {expected}")
    return panel


def add_smoke(panel: pd.DataFrame, *, this_year: int) -> tuple[pd.DataFrame, dict]:
    """Attribute smoke ourselves, then prefer ECHOLab where it published."""
    # A centered 3-year background window needs year+1, which does not exist
    # for the current year. Trailing window from this year onward.
    ours = attribute_smoke(
        panel[["location", "date", "pm25", "plume"]],
        trailing_only_from=this_year,
    )

    # A location with no monitor gets NO estimate from us -- not even a zero.
    #
    # `attribute_smoke` sets smoke to exactly 0 on non-plume days, which is
    # correct for a station that is actually measuring: no plume overhead means
    # no wildfire PM2.5, whatever the concentration was. But applied to a
    # location with no instrument at all it manufactures a long run of
    # confident zeros out of nothing.
    #
    # That is not a cosmetic problem. Those zeros are non-missing, so they sail
    # through the climatology's coverage gate in s05 and add whole years to the
    # denominator -- years in which every genuinely smoky day is NaN and so
    # contributes no exceedance. Napa, which lost its monitor in 2021, was
    # having its risk quietly diluted by three such years. Missing data must
    # never read as clean air; this is where that rule is enforced.
    ours.loc[ours["pm25"].isna(), "smoke_pm"] = np.nan
    panel = panel.merge(
        ours[["location", "date", "background", "pm25_anom", "smoke_pm"]]
        .rename(columns={"smoke_pm": "smoke_pm_ours"}),
        on=["location", "date"], how="left",
    )

    if ECHOLAB.exists():
        echo = pd.read_parquet(ECHOLAB).rename(
            columns={"smoke_pm": "smoke_pm_pub", "smoke_source": "pub_source"}
        )
        panel = panel.merge(echo, on=["location", "date"], how="left")
    else:
        panel["smoke_pm_pub"] = np.nan
        panel["pub_source"] = None

    have_pub = panel["smoke_pm_pub"].notna()
    panel["smoke_pm"] = np.where(
        have_pub, panel["smoke_pm_pub"], panel["smoke_pm_ours"]
    )
    panel["smoke_source"] = np.where(
        have_pub,
        panel["pub_source"].fillna("echolab"),
        np.where(panel["smoke_pm_ours"].notna(), "ours", "none"),
    )

    xval = cross_validate(panel)
    return panel.drop(columns=["pub_source"]), xval


def cross_validate(panel: pd.DataFrame) -> dict:
    """Compare our reimplementation against ECHOLab on their overlap."""
    both = panel[panel["smoke_pm_pub"].notna() & panel["smoke_pm_ours"].notna()]
    out: dict = {"n_overlap_days": int(len(both)), "by_location": {}}
    if both.empty:
        return out

    out["pearson_r_all"] = round(
        float(both["smoke_pm_pub"].corr(both["smoke_pm_ours"])), 4
    )
    out["mean_published"] = round(float(both["smoke_pm_pub"].mean()), 3)
    out["mean_ours"] = round(float(both["smoke_pm_ours"].mean()), 3)
    for slug, g in both.groupby("location"):
        if len(g) < 100 or g["smoke_pm_ours"].std() == 0:
            continue
        out["by_location"][slug] = {
            "n": int(len(g)),
            "r": round(float(g["smoke_pm_pub"].corr(g["smoke_pm_ours"])), 4),
            "mean_published": round(float(g["smoke_pm_pub"].mean()), 3),
            "mean_ours": round(float(g["smoke_pm_ours"].mean()), 3),
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--this-year", type=int, default=dt.date.today().year)
    args = ap.parse_args(argv)

    log("[s04] building the location x day panel")
    panel = build_panel()
    panel, xval = add_smoke(panel, this_year=args.this_year)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(OUT, index=False)
    XVAL_OUT.write_text(json.dumps(xval, indent=2) + "\n")

    log(f"[s04] wrote {OUT.name}: {len(panel):,} location-days, "
        f"{panel['date'].min().date()} -> {panel['date'].max().date()}")

    cov = panel.groupby("location").agg(
        pm25_days=("pm25", "count"),
        smoke_days=("smoke_pm", "count"),
        pct_pm25=("pm25", lambda s: f"{s.notna().mean():.0%}"),
    )
    log("  coverage by location:\n" + cov.to_string())
    log("  smoke source mix: "
        + json.dumps(panel["smoke_source"].value_counts().to_dict()))

    if xval.get("pearson_r_all") is not None:
        log(f"  cross-validation vs ECHOLab on {xval['n_overlap_days']:,} "
            f"overlapping days: r = {xval['pearson_r_all']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
