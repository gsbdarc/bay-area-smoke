"""Smoke-PM2.5 attribution, following Childs et al. (2022).

Reimplements the STATION-LEVEL half of the method. We deliberately do not
reimplement the gradient-boosted spatial interpolation that turns station
estimates into a national 10 km grid -- we only need point estimates at ten
locations, and the published grid already covers 2006-2023.

Reference implementation this follows:
  echolab-stanford/smokePM-version1.1
  scripts/main/04_01_calculate_station_smokePM_using_polygons.R
  nonsmoke_medians() in scripts/setup/00_02_load_functions.R

The method, in order:
  1. Complete location x day panel. Missing PM2.5 stays NaN -- NEVER zero.
  2. plume = 1 if any HMS smoke polygon covered the point that day.
  3. Background = median PM2.5 over non-smoke days, grouped by
     (location, calendar month), pooled across year-1, year, year+1.
  4. smokePM = plume ? max(pm25 - background, 0) : 0.

Step 3 is the subtle one and the easiest to get wrong: it is NOT a rolling
+/-N-day window. The background for Napa in September 2018 is the median of all
non-smoke-day PM2.5 at Napa across September 2017, 2018 and 2019 combined.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Minimum non-smoke observations behind a background median before we trust it.
# Childs et al. track nobs_3yr; we use it to blank out thin station-months
# rather than publishing an anomaly computed off a handful of days.
MIN_BACKGROUND_OBS = 10

# Smoke PM2.5 at or above this is "noticeable smoke" for the UI toggle.
NOTICEABLE_SMOKE = 5.0


def nonsmoke_background(
    df: pd.DataFrame,
    *,
    trailing_only_from: int | None = None,
) -> pd.DataFrame:
    """Station- and month-specific median PM2.5 over non-smoke days.

    Expects columns: location, date (datetime64), pm25 (float), plume (0/1/NaN).
    Returns one row per (location, year, month) with `background` and `nobs`.

    The window is CENTERED on `year` -- it pools year-1, year, year+1 -- which
    is impossible for the current year because year+1 has not happened. Pass
    `trailing_only_from=YYYY` to switch to a trailing window (year-2, year-1,
    year) at and after that year. Those values are provisional and must be
    labelled as such in the UI.
    """
    required = {"location", "date", "pm25", "plume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"nonsmoke_background missing columns: {sorted(missing)}")

    d = df.copy()
    d["year"] = d["date"].dt.year
    d["month"] = d["date"].dt.month

    # Non-smoke days only, and only days we actually measured. plume is NaN
    # where the HMS archive has no file -- those days are unusable as
    # background because we cannot rule out smoke.
    clean = d[(d["plume"] == 0) & d["pm25"].notna()]

    # Sum and count per cell, so pooling across years is a simple join. We need
    # the median, not the mean, so keep the raw values grouped.
    cells = (
        clean.groupby(["location", "year", "month"])["pm25"]
        .apply(list)
        .rename("vals")
        .reset_index()
    )
    lookup = {
        (r.location, r.year, r.month): r.vals for r in cells.itertuples(index=False)
    }

    # Emit a background for every (location, year, month) present in the input
    # panel, including ones with no clean observations of their own.
    grid = (
        d[["location", "year", "month"]]
        .drop_duplicates()
        .sort_values(["location", "year", "month"])
        .reset_index(drop=True)
    )

    backgrounds, counts = [], []
    for loc, year, month in grid.itertuples(index=False):
        if trailing_only_from is not None and year >= trailing_only_from:
            years = (year - 2, year - 1, year)
        else:
            years = (year - 1, year, year + 1)

        pooled: list[float] = []
        for y in years:
            pooled.extend(lookup.get((loc, y, month), ()))

        if len(pooled) >= MIN_BACKGROUND_OBS:
            backgrounds.append(float(np.median(pooled)))
        else:
            backgrounds.append(np.nan)
        counts.append(len(pooled))

    grid["background"] = backgrounds
    grid["nobs"] = counts
    return grid


def attribute_smoke(
    df: pd.DataFrame,
    *,
    trailing_only_from: int | None = None,
) -> pd.DataFrame:
    """Add `background`, `pm25_anom` and `smoke_pm` to a location x day panel.

    smoke_pm is 0 on non-smoke days BY CONSTRUCTION -- it is not estimated. It
    is NaN only where we could not determine smoke status or had no measurement,
    so that "we don't know" never renders as "clean".
    """
    d = df.copy()
    d["year"] = d["date"].dt.year
    d["month"] = d["date"].dt.month

    bg = nonsmoke_background(d, trailing_only_from=trailing_only_from)
    d = d.merge(bg, on=["location", "year", "month"], how="left")

    d["pm25_anom"] = d["pm25"] - d["background"]

    # Non-smoke day -> exactly zero smoke.
    smoke = pd.Series(0.0, index=d.index)
    # Smoke overhead -> the positive part of the anomaly. Negative anomalies on
    # smoke days are clipped to zero, per the reference implementation.
    on_smoke = d["plume"] == 1
    smoke[on_smoke] = d.loc[on_smoke, "pm25_anom"].clip(lower=0.0)
    # Unknown smoke status, unknown concentration, or untrustworthy background
    # -> unknown smoke. Do not fabricate a zero.
    unknown = d["plume"].isna() | (on_smoke & (d["pm25"].isna() | d["background"].isna()))
    smoke[unknown] = np.nan

    d["smoke_pm"] = smoke
    return d.drop(columns=["year", "month"])
