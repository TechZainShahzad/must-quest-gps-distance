"""Per-ping classification under R1-R6 and the assumptions confirmed at CHECKPOINT 1.

SYNTHETIC project (MUST Company hiring Quest). Used by qa_check.py to flag every
ping. It never reads data/fault_manifest.csv.

Order of operations (docs/assumptions.md):
  R3  duplicates: same (driver, ts) -> keep the copy closest to the road (item 4),
      tie -> first received
  R4  sort by (driver, ts); out_of_order = ts earlier than a ts already received
  9   trip break when the gap between consecutive received (deduplicated)
      timestamps is > 300 s; R2 anchor resets there
  R1  off_road_m > 100 -> rejected (stateless, evaluated first; item 3)
  R2  implied_kmh vs the last ACCEPTED ping of the same trip > 150 -> rejected
  R5  local_day on UTC+9, [00:00, 24:00)
  R6  in_zone = zone covers the ping (boundary counts as inside)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import shapely

from .geo import LocalFrame, distance_to, local_day

R1_MAX_M = 100.0
R2_MAX_KMH = 150.0
TRIP_BREAK_S = 300
ESTIMATED_S = 60


def classify(pings: pd.DataFrame, road_ll, zone_ll) -> pd.DataFrame:
    """Return one row per input ping (same order, `row_id` = input position)."""
    frame = LocalFrame(zone_ll)
    road, zone = frame.geom(road_ll), frame.geom(zone_ll)

    v = pings[["driver_id", "ts_utc", "lat", "lon"]].copy().reset_index(drop=True)
    v.insert(0, "row_id", np.arange(len(v)))
    v["ts"] = pd.to_datetime(v["ts_utc"], utc=True, format="%Y-%m-%dT%H:%M:%SZ")
    v["x"], v["y"] = frame.xy(v["lon"], v["lat"])
    v["off_road_m"] = distance_to(road, v["x"], v["y"])
    v["in_zone"] = shapely.covers_xy(zone, v["x"], v["y"]) if hasattr(shapely, "covers_xy") else \
        shapely.covers(zone, shapely.points(v["x"], v["y"]))
    v["local_day"] = local_day(v["ts"])
    v["utc_day"] = v["ts"].dt.strftime("%Y-%m-%d")

    # R4: out of order relative to what was already received for this driver
    prev_max = v.groupby("driver_id")["ts"].transform(lambda s: s.cummax().shift())
    v["out_of_order"] = (v["ts"] < prev_max).fillna(False).astype(bool)

    # R3: duplicates -> keep closest to road, tie -> first received (lowest row_id)
    order = v.sort_values(["driver_id", "ts", "off_road_m", "row_id"])
    keep_idx = order.drop_duplicates(["driver_id", "ts"], keep="first").index
    v["dup"] = ~v.index.isin(keep_idx)
    grp = v.groupby(["driver_id", "ts"])
    v["dup_group_size"] = grp["row_id"].transform("size")
    kept_xy = v.loc[keep_idx].set_index(["driver_id", "ts"])[["x", "y"]]
    kx = kept_xy.reindex(pd.MultiIndex.from_frame(v[["driver_id", "ts"]]))
    v["dup_spread_m"] = np.where(v["dup_group_size"] > 1,
                                 np.hypot(v["x"].values - kx["x"].values, v["y"].values - kx["y"].values), np.nan)
    v["dup_kept_row"] = np.where(v["dup"], v.loc[keep_idx].set_index(["driver_id", "ts"])["row_id"]
                                 .reindex(pd.MultiIndex.from_frame(v[["driver_id", "ts"]])).values, -1)

    # sequential pass per driver over the kept pings in timestamp order
    v["gap_s"] = np.nan
    v["implied_kmh"] = np.nan
    v["speed_ref_row"] = -1
    v["noise_reason"] = ""
    v["seg_flag"] = ""           # first / normal / estimated / trip_break   (accepted pings only)
    v["seg_dt_s"] = np.nan       # dt from the previous accepted ping (counted segments)
    v["trip_id"] = -1            # per driver; a new trip starts after a received gap > 300 s
    kept = v.loc[keep_idx].sort_values(["driver_id", "ts", "row_id"])
    for _, g in kept.groupby("driver_id", sort=False):
        anchor = None            # last accepted ping in the current trip
        had_accepted = False
        prev_ts = None
        trip = -1
        for i in g.index:
            r = v.loc[i]
            gap = None if prev_ts is None else (r.ts - prev_ts).total_seconds()
            prev_ts = r.ts
            v.at[i, "gap_s"] = np.nan if gap is None else gap
            trip_start = gap is None or gap > TRIP_BREAK_S
            if trip_start:
                anchor = None
                trip += 1
            v.at[i, "trip_id"] = trip
            reasons = []
            if r.off_road_m > R1_MAX_M:
                reasons.append("off_road")
            if anchor is not None:
                a = v.loc[anchor]
                dt = (r.ts - a.ts).total_seconds()
                kmh = float(np.hypot(r.x - a.x, r.y - a.y) / dt * 3.6)
                v.at[i, "implied_kmh"] = kmh
                v.at[i, "speed_ref_row"] = int(a.row_id)
                if kmh > R2_MAX_KMH:
                    reasons.append("speed")
            if reasons:
                v.at[i, "noise_reason"] = "+".join(reasons)
                continue
            if anchor is None:
                # first accepted ping of a trip; "trip_break" = an earlier trip existed, so the
                # segment back to it is not counted (item 9)
                v.at[i, "seg_flag"] = "trip_break" if had_accepted else "first"
            else:
                dt = (r.ts - v.at[anchor, "ts"]).total_seconds()
                v.at[i, "seg_dt_s"] = dt
                v.at[i, "seg_flag"] = "estimated" if dt > ESTIMATED_S else "normal"
            anchor = i
            had_accepted = True

    # duplicate copies belong to the same trip as the copy that was kept
    tid = v.loc[keep_idx].set_index(["driver_id", "ts"])["trip_id"]
    v["trip_id"] = tid.reindex(pd.MultiIndex.from_frame(v[["driver_id", "ts"]])).values.astype(int)

    v["status"] = np.where(v["dup"], "duplicate", np.where(v["noise_reason"] != "", "rejected", "accepted"))
    reason = np.where(v["dup"], "R3 duplicate of row " + v["dup_kept_row"].astype(str), "")
    reason = np.where(v["noise_reason"].str.contains("off_road"),
                      "R1 " + v["off_road_m"].round(2).astype(str) + " m from road > 100", reason)
    reason = np.where(v["noise_reason"] == "speed",
                      "R2 " + v["implied_kmh"].round(2).astype(str) + " km/h vs row " + v["speed_ref_row"].astype(str) + " > 150",
                      reason)
    reason = np.where(v["status"] == "accepted", "accepted (" + v["seg_flag"] + ")", reason)
    v["reason"] = reason
    return v.drop(columns=["ts"])
