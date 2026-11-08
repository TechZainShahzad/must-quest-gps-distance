"""Shared helpers for the regression suite. SYNTHETIC: MUST Company hiring Quest.

Fixtures are built in metres in a local frame (x east, y north) around a fictional
point near Seoul, then converted to lon/lat, so every expected value is hand-checkable:

  road  = straight line y = 0 from x = -3000 m to x = +3000 m
  zone  = rectangle x in [-1000, 1000] m, y in [-500, 500] m
  time  = 2026-10-12T01:00:00Z + s  (10:00 local; UTC date and local day agree)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from pyproj import Transformer
from shapely.geometry import LineString, Polygon

from src import distance_fixed, reference_buggy

LAT0, LON0 = 37.5, 127.0
_inv = Transformer.from_crs(f"+proj=aeqd +lat_0={LAT0} +lon_0={LON0} +datum=WGS84 +units=m +no_defs", "EPSG:4326",
                            always_xy=True)
BASE = datetime(2026, 10, 12, 1, 0, 0, tzinfo=timezone.utc)
TOL_M = 1.0


def ll(x: float, y: float):
    lon, lat = _inv.transform(x, y)
    return round(lat, 7), round(lon, 7)


ROAD = LineString([_inv.transform(x, 0.0) for x in (-3000.0, 3000.0)])
ZONE = Polygon([_inv.transform(x, y) for x, y in [(-1000, -500), (1000, -500), (1000, 500), (-1000, 500)]])


def iso(s: float | str) -> str:
    if isinstance(s, str):
        return s
    return (BASE + timedelta(seconds=s)).strftime("%Y-%m-%dT%H:%M:%SZ")


def pings(*rows, driver="T01") -> pd.DataFrame:
    """rows = (t, x, y) in RECEIVED order; t is seconds after BASE or an ISO string."""
    out = []
    for t, x, y in rows:
        lat, lon = ll(x, y)
        out.append({"driver_id": driver, "ts_utc": iso(t), "lat": lat, "lon": lon})
    return pd.DataFrame(out)


def run(impl, df) -> dict:
    """Metres per (driver, day) from either implementation."""
    out = impl.compute(df, ROAD, ZONE)
    day = "local_day" if "local_day" in out else "day"
    metres = out["m"] if "m" in out else out["km"] * 1000
    return {(d, k): float(m) for d, k, m in zip(out["driver_id"], out[day], metres)}


def assert_matches(got: dict, expected: dict):
    keys = set(got) | set(expected)
    diffs = {k: got.get(k, 0.0) - expected.get(k, 0.0) for k in keys}
    bad = {k: round(v, 3) for k, v in diffs.items() if abs(v) > TOL_M}
    assert not bad, f"off by more than {TOL_M} m: {bad} (got {got}, expected {expected})"


def both(buggy_fails: bool, reason: str = ""):
    """Parametrise a test over both implementations and declare the expected outcome:
    distance_fixed must pass; reference_buggy must fail (strict xfail) unless buggy_fails=False."""
    buggy_marks = [pytest.mark.xfail(strict=True, raises=AssertionError, reason=f"old behaviour: {reason}")] if buggy_fails else []
    return pytest.mark.parametrize("impl", [
        pytest.param(reference_buggy, id="reference_buggy", marks=buggy_marks),
        pytest.param(distance_fixed, id="distance_fixed"),
    ])
