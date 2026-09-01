"""PM2.5 -> AQI conversion using the CURRENT (2024) EPA breakpoints.

Why we recompute instead of using EPA's stored `AQI` column: EPA revised the
PM2.5 AQI breakpoints effective 2024-05-06 (89 FR 16202). Values stored in the
historical files were computed under whichever breakpoints were in force at the
time, so the stored column is NOT comparable across years -- the same 30 ug/m3
day is AQI 88 under the old scale and AQI 89 under the new one, and at the low
end the divergence is much larger (12.0 vs 9.0 for the Good/Moderate line).

Recomputing every year from raw concentration puts the whole record on one
scale, which is the only way a multi-decade trend chart means anything.
"""
from __future__ import annotations

import numpy as np

# (C_low, C_high, I_low, I_high) for 24-hour PM2.5, effective 2024-05-06.
BREAKPOINTS: tuple[tuple[float, float, int, int], ...] = (
    (0.0, 9.0, 0, 50),
    (9.1, 35.4, 51, 100),
    (35.5, 55.4, 101, 150),
    (55.5, 125.4, 151, 200),
    (125.5, 225.4, 201, 300),
    (225.5, 325.4, 301, 500),
)

CATEGORIES: tuple[tuple[int, str], ...] = (
    (50, "Good"),
    (100, "Moderate"),
    (150, "Unhealthy for Sensitive Groups"),
    (200, "Unhealthy"),
    (300, "Very Unhealthy"),
    (500, "Hazardous"),
)

# The line above which outdoor events become a genuinely bad idea for guests
# with any respiratory sensitivity.
USG_THRESHOLD = 101


def pm25_to_aqi(conc):
    """Convert 24-hour PM2.5 (ug/m3) to AQI. Scalar or array-like.

    EPA requires truncating the concentration to one decimal place BEFORE
    applying the piecewise-linear formula; skipping that shifts values by a
    point near breakpoints. Concentrations above the top breakpoint are capped
    at 500 ("beyond the AQI"). Negative values -- which do occur in raw monitor
    data as instrument noise near zero -- are clamped to 0, not dropped.
    """
    c = np.asarray(conc, dtype="float64")
    scalar = c.ndim == 0
    c = np.atleast_1d(c)

    out = np.full(c.shape, np.nan)
    valid = ~np.isnan(c)

    # Truncate (not round) to 1dp, per EPA. Clamp negatives to zero first so
    # truncation of -0.04 does not produce -0.1.
    trunc = np.floor(np.maximum(c[valid], 0.0) * 10.0) / 10.0
    res = np.full(trunc.shape, np.nan)

    for c_lo, c_hi, i_lo, i_hi in BREAKPOINTS:
        m = (trunc >= c_lo) & (trunc <= c_hi)
        res[m] = (i_hi - i_lo) / (c_hi - c_lo) * (trunc[m] - c_lo) + i_lo

    # Anything above the top breakpoint is off the scale.
    res[trunc > BREAKPOINTS[-1][1]] = 500.0

    out[valid] = np.round(res)
    return float(out[0]) if scalar else out


def aqi_category(aqi):
    """Map AQI to its category name. Scalar or array-like."""
    a = np.asarray(aqi, dtype="float64")
    scalar = a.ndim == 0
    a = np.atleast_1d(a)

    out = np.full(a.shape, None, dtype=object)
    for i, v in enumerate(a):
        if np.isnan(v):
            out[i] = None
            continue
        for upper, name in CATEGORIES:
            if v <= upper:
                out[i] = name
                break
        else:
            out[i] = "Hazardous"
    return out[0] if scalar else out
