"""AFTER version: corrected in-zone distance per driver per local day.

SYNTHETIC project (MUST Company hiring Quest). Follows R1-R6 and the assumptions
Zain confirmed at CHECKPOINT 1 (docs/assumptions.md). Pure function: no file I/O.

Pipeline (each step fixes one problem from docs/findings.md):
  classify()            R3 dedup (closest to road), R4 sort by ts, trips (gap > 300 s),
                        R1 off-road, R2 speed vs last accepted ping          P6 P1 P2 P3
  segments              consecutive ACCEPTED pings of the same trip only     P1 P2
  split_at_midnight     cut at 00:00 UTC+9, proportionally by time           P5
  clip                  only the part of the segment inside the zone         P4
  metric                lengths in the local AEQD projection (not haversine) P7
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from shapely.geometry import LineString

from .geo import LOCAL_OFFSET_S, LocalFrame
from .ping_rules import ESTIMATED_S, classify

DAY_S = 86400


def local_day_of(t: float) -> str:
    return pd.Timestamp(int(np.floor(t)) + LOCAL_OFFSET_S, unit="s").strftime("%Y-%m-%d")


def split_at_midnight(t0: float, t1: float, p0, p1):
    """Yield (local_day, start_xy, end_xy). A day is [00:00, 24:00) local; the split
    point is interpolated by time (constant speed within the segment)."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    a, pa = t0, p0
    cut = ((a + LOCAL_OFFSET_S) // DAY_S + 1) * DAY_S - LOCAL_OFFSET_S
    while cut < t1:
        pb = p0 + (p1 - p0) * (cut - t0) / (t1 - t0)
        yield local_day_of(a), pa, pb
        a, pa, cut = cut, pb, cut + DAY_S
    yield local_day_of(a), pa, p1


def segments(pings: pd.DataFrame, road, zone, validation: pd.DataFrame | None = None) -> pd.DataFrame:
    """Counted segment pieces: driver_id, local_day, from_row, to_row, dt_s, length_m, inside_m, estimated."""
    v = classify(pings, road, zone) if validation is None else validation
    zone_xy = LocalFrame(zone).geom(zone)
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    acc = v[v.status == "accepted"].assign(t=lambda d: (pd.to_datetime(d.ts_utc, utc=True) - epoch).dt.total_seconds())
    acc = acc.sort_values(["driver_id", "t", "row_id"])
    rows = []
    for d, g in acc.groupby("driver_id", sort=True):
        r = g.to_dict("records")
        for a, b in zip(r, r[1:]):
            if a["trip_id"] != b["trip_id"]:
                continue                                   # trip break: not counted (item 9)
            dt = b["t"] - a["t"]
            for day, pa, pb in split_at_midnight(a["t"], b["t"], (a["x"], a["y"]), (b["x"], b["y"])):
                seg = LineString([pa, pb])
                inside = seg.intersection(zone_xy).length if seg.length > 0 else 0.0
                rows.append((d, day, a["row_id"], b["row_id"], dt, seg.length, inside, dt > ESTIMATED_S))
    return pd.DataFrame(rows, columns=["driver_id", "local_day", "from_row", "to_row", "dt_s", "length_m", "inside_m", "estimated"])


def compute(pings: pd.DataFrame, road, zone, validation: pd.DataFrame | None = None) -> pd.DataFrame:
    """km per driver per local day (UTC+9).

    Input: pings with driver_id, ts_utc (ISO 8601 Z), lat, lon in any order; road
    (LineString) and zone (Polygon) in lon/lat WGS84. Output columns: driver_id,
    local_day, km (3 decimals, for display), m (unrounded metres, for comparison),
    estimated_km (part of km from segments with dt > 60 s).
    """
    v = classify(pings, road, zone) if validation is None else validation
    seg = segments(pings, road, zone, v)
    days = v[v.status == "accepted"][["driver_id", "local_day"]].drop_duplicates()
    agg = seg.groupby(["driver_id", "local_day"]).agg(m=("inside_m", "sum")).reset_index()
    est = seg[seg.estimated].groupby(["driver_id", "local_day"]).agg(estimated_m=("inside_m", "sum")).reset_index()
    out = (days.merge(agg, how="outer", on=["driver_id", "local_day"])
               .merge(est, how="left", on=["driver_id", "local_day"]).fillna({"m": 0.0, "estimated_m": 0.0}))
    out["km"] = (out.m / 1000).round(3)
    out["estimated_km"] = (out.estimated_m / 1000).round(3)
    return out.sort_values(["driver_id", "local_day"])[["driver_id", "local_day", "km", "m", "estimated_km"]].reset_index(drop=True)
