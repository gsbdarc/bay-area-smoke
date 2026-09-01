"""AQI conversion tests, anchored on the 2024 EPA breakpoints."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from aqi import aqi_category, pm25_to_aqi  # noqa: E402


@pytest.mark.parametrize(
    "conc,expected",
    [
        (0.0, 0),
        (9.0, 50),      # top of Good, revised down from 12.0 in 2024
        (9.1, 51),      # bottom of Moderate
        (35.4, 100),
        (35.5, 101),    # bottom of Unhealthy for Sensitive Groups
        (55.4, 150),
        (55.5, 151),    # bottom of Unhealthy
        (125.4, 200),
        (125.5, 201),   # bottom of Very Unhealthy
        (225.4, 300),
        (225.5, 301),   # bottom of Hazardous
        (325.4, 500),
    ],
)
def test_breakpoint_boundaries_are_exact(conc, expected):
    assert pm25_to_aqi(conc) == expected


def test_interpolates_within_a_band():
    # (100-51)/(35.4-9.1) * (12.0-9.1) + 51 = 56.4 -> 56
    assert pm25_to_aqi(12.0) == 56


def test_truncates_rather_than_rounds():
    # EPA truncates concentration to 1dp before applying the formula. 9.09
    # must land in the Good band as 9.0, not round up into Moderate.
    assert pm25_to_aqi(9.09) == 50
    assert pm25_to_aqi(9.19) == 51


def test_above_scale_caps_at_500():
    assert pm25_to_aqi(400.0) == 500
    assert pm25_to_aqi(1000.0) == 500


def test_negative_noise_clamps_to_zero_not_nan():
    # Raw monitor data really does contain small negatives near zero. They are
    # instrument noise, not missing data, and must not become NaN.
    assert pm25_to_aqi(-0.4) == 0


def test_nan_propagates():
    assert np.isnan(pm25_to_aqi(np.nan))


def test_vectorized_matches_scalar():
    vals = [0.0, 9.0, 12.0, 55.5, 300.0, np.nan]
    arr = pm25_to_aqi(vals)
    for v, got in zip(vals, arr):
        if np.isnan(v):
            assert np.isnan(got)
        else:
            assert got == pm25_to_aqi(v)


def test_categories():
    assert aqi_category(50) == "Good"
    assert aqi_category(51) == "Moderate"
    assert aqi_category(101) == "Unhealthy for Sensitive Groups"
    assert aqi_category(200) == "Unhealthy"
    assert aqi_category(301) == "Hazardous"
    assert aqi_category(np.nan) is None
