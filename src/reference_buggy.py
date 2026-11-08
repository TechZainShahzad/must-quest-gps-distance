"""BEFORE version: the naive distance calculation. **Intentionally wrong.**

SYNTHETIC project (MUST Company hiring Quest). This is the "before" reference the QA
tool and the regression suite are run against. It is written the way a team might
plausibly ship it; the defects are listed here so nobody mistakes it for the fix:

  - sums haversine distance between consecutive rows in FILE order (no sort)  -> R4
  - no off-road or speed filtering                                           -> R1, R2
  - duplicate rows are counted                                               -> R3
  - groups by the UTC date of the ping                                       -> R5
  - counts the whole segment if EITHER endpoint is inside the zone           -> R6

Do not fix this file; the corrected implementation is src/distance_fixed.py.
"""
from __future__ import annotations

import json
import math

import pandas as pd
from shapely.geometry import Point, shape

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def load_zone(path):
    with open(path, encoding="utf-8") as fh:
        return shape(json.load(fh)["features"][0]["geometry"])


def segments(pings: pd.DataFrame, zone) -> pd.DataFrame:
    """One row per consecutive pair of pings of the same driver, in file order."""
    rows = []
    for driver, g in pings.groupby("driver_id", sort=True):
        prev = None
        for row_id, p in g.iterrows():
            inside = zone.contains(Point(p.lon, p.lat))
            if prev is not None:
                prev_id, q, q_inside = prev
                km = haversine_km(q.lat, q.lon, p.lat, p.lon)
                rows.append({
                    "driver_id": driver,
                    "from_row": prev_id,
                    "to_row": row_id,
                    "day": p.ts_utc[:10],                  # UTC date of the ping
                    "km": km,
                    "counted": q_inside or inside,         # in the zone if either end is
                })
            prev = (row_id, p, inside)
    return pd.DataFrame(rows, columns=["driver_id", "from_row", "to_row", "day", "km", "counted"])


def compute(pings: pd.DataFrame, road=None, zone=None) -> pd.DataFrame:
    """Return km per driver per day: columns driver_id, day, km.

    `pings` has columns driver_id, ts_utc, lat, lon (row order = file order).
    `road` is accepted for interface parity with distance_fixed and ignored.
    """
    seg = segments(pings, zone)
    seg = seg[seg["counted"]]
    out = seg.groupby(["driver_id", "day"], as_index=False)["km"].sum()
    out["km"] = out["km"].round(3)
    return out


if __name__ == "__main__":
    import sys

    df = pd.read_csv(sys.argv[1] if len(sys.argv) > 1 else "data/pings.csv")
    z = load_zone(sys.argv[2] if len(sys.argv) > 2 else "data/zone.geojson")
    print(compute(df, zone=z).to_string(index=False))
