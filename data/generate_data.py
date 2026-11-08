"""SYNTHETIC: generated for MUST Company hiring Quest, not real data.

Generates the synthetic GPS ping dataset, the injected-fault ground truth and the
truth distance table. Fully deterministic (fixed seed), runs offline.

Outputs (all in data/):
  road.geojson          road path (LineString, lon/lat WGS84)
  zone.geojson          campaign zone (Polygon, lon/lat WGS84)
  pings.csv             driver_id, ts_utc, lat, lon -- in RECEIVED order (not sorted)
  fault_manifest.csv    row_id, fault_type, detail, value  (tools must NOT read this)
  truth_distance.csv    true in-zone distance per driver per local day (UTC+9)
  sanity_plot.png       map of road, zone, pings coloured by injected fault

row_id = 0-based index of the data row in pings.csv (header excluded).

Truth (docs/assumptions.md item 11, as decided at CHECKPOINT 1) is computed here by
separate code that does NOT import anything from src/. It applies the rules with
perfect knowledge of what was injected: it drops the noise pings it injected, keeps
the near-miss pings the rules accept (at their reported position), uses the
original copy of each duplicate, and orders by timestamp. Geometry model is the
same as the confirmed assumptions: straight segments in a local azimuthal
equidistant projection centred on the zone centroid, split at local midnight,
clipped to the zone, no distance across a received-timestamp gap > 300 s.
`road_km` is the physical along-road in-zone distance from the 1 s simulation;
`trip_break_road_km` is the part of it driven during in-trip gaps > 5 min (not paid).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, Polygon

SEED = 20261104
OUT = Path(__file__).resolve().parent

LOCAL_OFFSET_S = 9 * 3600          # R5: local time is UTC+9
PING_S = 10                        # nominal ping interval
TRIP_BREAK_S = 300                 # item 9 (decided): gap > 5 min ends the trip
R1_MAX_M = 100.0                   # R1
R2_MAX_KMH = 150.0                 # R2
COORD_DP = 7                       # decimals written to pings.csv / geojson (~1 cm)
DESIGN_LAT0, DESIGN_LON0 = 37.50, 127.00   # fictional area near Seoul (UTC+9)
LOCAL_DAYS = ["2026-10-12", "2026-10-13", "2026-10-14"]
DRIVERS = [f"D{i:02d}" for i in range(1, 7)]
EDGE_TAG_M = 3.0                   # "just inside/outside" = within 3 m of the zone edge


def aeqd(lat0: float, lon0: float) -> str:
    return f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +datum=WGS84 +units=m +no_defs"


def iso(t: int) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_day(t: float) -> str:
    return datetime.fromtimestamp(int(np.floor(t)) + LOCAL_OFFSET_S, tz=timezone.utc).strftime("%Y-%m-%d")


def local_to_utc(day: str, hh: int, mm: int, ss: int = 0) -> int:
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(d.timestamp()) + hh * 3600 + mm * 60 + ss - LOCAL_OFFSET_S


# --------------------------------------------------------------------------- geometry

class Frame:
    """Local metric frame (AEQD) + the lon/lat rounding every reported ping goes through."""

    def __init__(self, lat0: float, lon0: float):
        self.fwd = Transformer.from_crs("EPSG:4326", aeqd(lat0, lon0), always_xy=True)
        self.inv = Transformer.from_crs(aeqd(lat0, lon0), "EPSG:4326", always_xy=True)

    def report(self, x, y):
        """Metric point -> rounded lon/lat (as written to CSV) -> back to metric."""
        lon, lat = self.inv.transform(x, y)
        lon, lat = np.round(lon, COORD_DP), np.round(lat, COORD_DP)
        xr, yr = self.fwd.transform(lon, lat)
        return lon, lat, xr, yr


def design_geometry(rng):
    xs = np.arange(-7000.0, 7000.1, 20.0)
    ys = 1100 * np.sin(2 * np.pi * xs / 3200) + 180 * np.sin(2 * np.pi * xs / 1150 + 0.7)
    bx = np.linspace(-5200, 5200, 9)
    by = rng.uniform(-120, 120, len(bx))
    zone = [(x, y) for x, y in zip(bx, by)] + [(5200.0, 2600.0), (-5200.0, 2600.0)]
    return np.column_stack([xs, ys]), np.array(zone)


def write_geojson(path: Path, geom_type: str, coords, name: str):
    fc = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"name": name, "label": "SYNTHETIC: generated for MUST Company hiring Quest, not real data"},
            "geometry": {"type": geom_type, "coordinates": coords},
        }],
    }
    path.write_text(json.dumps(fc, indent=1), encoding="utf-8", newline="\n")


class Road:
    """Road polyline in the truth frame with fast arc-length lookups."""

    def __init__(self, xy: np.ndarray):
        self.xy = xy
        seg = np.hypot(*np.diff(xy, axis=0).T)
        self.cum = np.concatenate([[0.0], np.cumsum(seg)])
        self.L = float(self.cum[-1])
        self.line = LineString(xy)

    def at(self, s):
        return np.interp(s, self.cum, self.xy[:, 0]), np.interp(s, self.cum, self.xy[:, 1])

    def normal(self, s: float):
        i = int(np.clip(np.searchsorted(self.cum, s) - 1, 0, len(self.xy) - 2))
        dx, dy = self.xy[i + 1] - self.xy[i]
        n = np.hypot(dx, dy)
        return -dy / n, dx / n

    def dist(self, x, y):
        return shapely.distance(self.line, shapely.points(x, y))


def fold(s: np.ndarray, L: float) -> np.ndarray:
    """U-turn at both road ends: map an unbounded arc position onto [0, L]."""
    m = np.mod(s, 2 * L)
    return np.where(m <= L, m, 2 * L - m)


# --------------------------------------------------------------------------- simulation

@dataclass
class Trip:
    tid: int
    driver: str
    t0: int                      # UTC epoch seconds of first ping
    dur: int                     # seconds
    s0: float
    direction: int
    v: np.ndarray                # speed per second (m/s), len dur+1
    tag: str = ""                # "midnight" / "midnight_exact"
    s_dense: np.ndarray = field(default=None)   # arc position per second
    missing: list = field(default_factory=list)  # (k_start, k_end_exclusive, kind)

    def simulate(self, L):
        cum = np.concatenate([[0.0], np.cumsum(self.v[:-1])])
        self.s_unfolded = self.s0 + self.direction * cum
        self.s_dense = fold(self.s_unfolded, L)

    @property
    def n_pings(self):
        return self.dur // PING_S + 1


def speed_profile(rng, dur: int) -> np.ndarray:
    v = np.zeros(dur + 1)
    cur, target, t_next = 0.0, 0.0, 0
    for t in range(dur + 1):
        if t >= t_next:
            if rng.random() < 0.12:
                target, hold = 0.0, int(rng.integers(15, 60))
            else:
                target, hold = rng.uniform(20, 75) / 3.6, int(rng.integers(30, 120))
            t_next = t + hold
        if dur - t < 25:
            target = 0.0
        cur = max(0.0, cur + float(np.clip(target - cur, -2.5, 2.0)))
        v[t] = cur
    return v


def schedule(rng, L) -> list[Trip]:
    trips: list[Trip] = []

    def add(driver, t0, dur, tag=""):
        trips.append(Trip(len(trips), driver, t0, dur, float(rng.uniform(0.15 * L, 0.85 * L)),
                          int(rng.choice([-1, 1])), speed_profile(rng, dur), tag))

    for day in LOCAL_DAYS:
        for d in DRIVERS:
            add(d, local_to_utc(day, int(rng.integers(7, 10)), int(rng.integers(0, 60)), int(rng.integers(0, 60))),
                int(rng.integers(20, 46)) * 60)
            add(d, local_to_utc(day, int(rng.integers(13, 17)), int(rng.integers(0, 60)), int(rng.integers(0, 60))),
                int(rng.integers(20, 46)) * 60)
    # trips across 00:00 UTC+9 (= 15:00 UTC) on the first two nights
    for day, drivers, exact in [(LOCAL_DAYS[0], ["D01", "D02", "D03"], "D02"), (LOCAL_DAYS[1], ["D04", "D05"], "D05")]:
        midnight = local_to_utc(day, 24, 0)
        for d in drivers:
            if d == exact:
                t0, tag = midnight - PING_S * int(rng.integers(40, 100)), "midnight_exact"
            else:
                while True:
                    t0 = midnight - int(rng.integers(400, 1100))
                    if (midnight - t0) % PING_S:
                        break
                tag = "midnight"
            add(d, t0, int(rng.integers(28, 46)) * 60, tag)
    trips.sort(key=lambda tr: (tr.driver, tr.t0))
    for i, tr in enumerate(trips):
        tr.tid = i
    return trips


def force_zone_edge(trip: Trip, road: Road, zone: Polygon, rng) -> bool:
    """Shift the trip's start so one ping lands 0.5-3 m (along road) from a zone crossing."""
    k_idx = np.arange(trip.n_pings) * PING_S
    s_unf = trip.s_unfolded[k_idx]
    x, y = road.at(trip.s_dense[k_idx])
    inside = shapely.contains_xy(zone, x, y)
    flips = np.nonzero(inside[1:] != inside[:-1])[0]
    for k in flips:
        if k < 3 or not (0 < s_unf[: k + 2].min() and s_unf[: k + 2].max() < road.L):
            continue
        seg = LineString([(x[k], y[k]), (x[k + 1], y[k + 1])])
        cross = seg.intersection(zone.exterior)
        if cross.is_empty:
            continue
        s_c = road.line.project(cross.representative_point())
        delta = rng.uniform(0.5, 3.0) * rng.choice([-1, 1])
        new_s0 = trip.s0 + (s_c - trip.direction * delta) - s_unf[k]
        if 0.05 * road.L < new_s0 < 0.95 * road.L:
            trip.s0 = float(new_s0)
            trip.simulate(road.L)
            return True
    return False


# --------------------------------------------------------------------------- pings & faults

@dataclass
class Ping:
    uid: int
    driver: str
    ts: int
    lon: float
    lat: float
    x: float                     # reported position in the truth frame (after rounding)
    y: float
    trip: int
    k: int
    role: str = "clean"          # clean | noise_* | nearmiss_* | dup_exact | dup_coord
    detail: str = ""
    value: float = np.nan
    recv: float = 0.0
    late: str = ""               # "", "late", "very_late"
    late_s: float = np.nan


def build(rng):
    # 1. geometry in a design frame -> rounded lon/lat files -> truth frame from the zone centroid
    road_d, zone_d = design_geometry(rng)
    design = Frame(DESIGN_LAT0, DESIGN_LON0)
    rlon, rlat, _, _ = design.report(road_d[:, 0], road_d[:, 1])
    zlon, zlat, _, _ = design.report(zone_d[:, 0], zone_d[:, 1])
    road_ll = [[float(a), float(b)] for a, b in zip(rlon, rlat)]
    zone_ring = [[float(a), float(b)] for a, b in zip(zlon, zlat)]
    zone_ring.append(zone_ring[0])
    write_geojson(OUT / "road.geojson", "LineString", road_ll, "synthetic road path")
    write_geojson(OUT / "zone.geojson", "Polygon", [zone_ring], "synthetic campaign zone")

    c = Polygon(zone_ring).centroid            # projection centre: zone centroid in lon/lat degrees
    F = Frame(c.y, c.x)
    road = Road(np.column_stack(F.fwd.transform(rlon, rlat)))
    zone = Polygon(np.column_stack(F.fwd.transform([p[0] for p in zone_ring], [p[1] for p in zone_ring])))

    # 2. trips
    trips = schedule(rng, road.L)
    for tr in trips:
        tr.simulate(road.L)
    forced_edge = set()
    for tr in trips:
        if rng.random() < 0.4 and force_zone_edge(tr, road, zone, rng):
            forced_edge.add(tr.tid)

    # 3. missing pings (gaps) inside trips
    for tr in trips:
        n = tr.n_pings
        # keep the midnight crossing (and the exact 15:00:00Z ping) free of gaps
        k_mid = (local_to_utc(local_day(tr.t0), 24, 0) - tr.t0) / PING_S if tr.tag else -99
        for kind, p, lo, hi in [("gap_short", 0.45, 2, 5), ("gap_estimated", 0.35, 7, 28), ("gap_trip_break", 0.12, 32, 60)]:
            if rng.random() < p and n > hi + 30:
                for _ in range(20):
                    ln = int(rng.integers(lo, hi + 1))
                    a = int(rng.integers(8, n - ln - 8))
                    if (all(a + ln + 6 < m0 or m1 + 6 < a for m0, m1, _ in tr.missing)
                            and not (a - 6 <= k_mid <= a + ln + 6)):
                        tr.missing.append((a, a + ln, kind))
                        break

    # 4. clean reported pings
    pings: list[Ping] = []
    by_trip: dict[int, list[Ping]] = {}
    for tr in trips:
        miss = {k for a, b, _ in tr.missing for k in range(a, b)}
        ks = [k for k in range(tr.n_pings) if k not in miss]
        sx, sy = road.at(tr.s_dense[np.array(ks) * PING_S])
        lon, lat, xr, yr = F.report(sx, sy)
        lst = []
        for i, k in enumerate(ks):
            p = Ping(len(pings), tr.driver, tr.t0 + k * PING_S, float(lon[i]), float(lat[i]), float(xr[i]), float(yr[i]), tr.tid, k)
            pings.append(p)
            lst.append(p)
        by_trip[tr.tid] = lst

    trip_of = {tr.tid: tr for tr in trips}

    # zone-edge tags on clean pings (forced or natural)
    edge_dist = shapely.distance(zone.exterior, shapely.points([p.x for p in pings], [p.y for p in pings]))
    edge_inside = shapely.contains_xy(zone, [p.x for p in pings], [p.y for p in pings])
    edge_tags = {}
    for p, dist, ins in zip(pings, edge_dist, edge_inside):
        if dist <= EDGE_TAG_M:
            edge_tags[p.uid] = ("zone_edge_inside" if ins else "zone_edge_outside",
                                "forced" if p.trip in forced_edge else "natural", float(dist))

    # midnight tags
    midnight_tags = {}
    for tr in trips:
        if tr.tag.startswith("midnight"):
            lst = by_trip[tr.tid]
            for p in lst:
                if local_day(p.ts) != local_day(lst[0].ts):
                    midnight_tags[p.uid] = ("midnight_cross", "exact_0000" if (p.ts + LOCAL_OFFSET_S) % 86400 == 0 else "inexact")
                    break

    # 5. noise / near-miss injection on clean pings
    def eligible(p_list, i):
        p = p_list[i]
        if i < 4 or i > len(p_list) - 6:
            return False
        if p_list[i].ts - p_list[i - 1].ts != PING_S or p_list[i + 1].ts - p_list[i].ts != PING_S:
            return False
        if any(abs(p_list[j].ts - p.ts) > 3 * PING_S for j in (i - 3, i + 3)):   # not within 3 of a gap
            return False
        near = {p_list[j].uid for j in range(i - 1, i + 2)}
        return not (near & (set(edge_tags) | set(midnight_tags)))

    cands = [(tid, i) for tid, lst in by_trip.items() for i in range(len(lst)) if eligible(lst, i)]
    order = rng.permutation(len(cands))
    used: dict[int, list[int]] = {}
    plan = (["noise_offroad:big"] * 40 + ["noise_offroad:just_over"] * 16 + ["nearmiss_offroad:just_under"] * 16
            + ["noise_speed:big"] * 30 + ["noise_speed:just_over"] * 16 + ["nearmiss_speed:just_under"] * 16)
    plan_i = 0
    speed_kmh = lambda a, b, dt: float(np.hypot(b[0] - a[0], b[1] - a[1]) / dt * 3.6)

    for oi in order:
        if plan_i >= len(plan):
            break
        tid, i = cands[oi]
        if any(abs(i - j) < 4 for j in used.get(tid, [])):
            continue
        lst = by_trip[tid]
        p, prev, nxt = lst[i], lst[i - 1], lst[i + 1]
        tr = trip_of[tid]
        role, detail = plan[plan_i].split(":")
        res = None
        if role in ("noise_offroad", "nearmiss_offroad"):
            s_k = float(tr.s_dense[p.k * PING_S])
            px, py = road.at(s_k)
            if detail == "big":
                for _ in range(10):
                    ang, d = rng.uniform(0, 2 * np.pi), rng.uniform(300, 2500)
                    lon, lat, xr, yr = F.report(px + d * np.cos(ang), py + d * np.sin(ang))
                    m = float(road.dist(xr, yr))
                    if m > 200:
                        res = (lon, lat, xr, yr, m)
                        break
            else:
                lo, hi, target = ((100.15, 103.0, rng.uniform(100.3, 102.5)) if role == "noise_offroad"
                                  else (96.5, 99.85, rng.uniform(97.0, 99.6)))
                nx, ny = road.normal(s_k)
                for side in rng.permutation([1, -1]):
                    d = target
                    for _ in range(8):
                        lon, lat, xr, yr = F.report(px + side * nx * d, py + side * ny * d)
                        m = float(road.dist(xr, yr))
                        if lo < m < hi:
                            res = (lon, lat, xr, yr, m)
                            break
                        d += target - m
                    if res:
                        break
            if res and role == "nearmiss_offroad":
                # accepted ping: must also pass R2 both ways, or it is not a clean near-miss
                if speed_kmh((prev.x, prev.y), res[2:4], PING_S) > 140 or speed_kmh(res[2:4], (nxt.x, nxt.y), PING_S) > 140:
                    res = None
            value = res[4] if res else None
        else:
            lo, hi, target = {"big": (160.0, 1e9, rng.uniform(170, 400)),
                              "just_over": (150.2, 153.0, rng.uniform(150.4, 152.5)),
                              "just_under": (146.0, 149.8, rng.uniform(146.5, 149.5))}[detail]
            s_prev = float(tr.s_dense[prev.k * PING_S])
            s_k = float(tr.s_dense[p.k * PING_S])
            dirs = [1 if s_k >= s_prev else -1]
            dirs.append(-dirs[0])
            want = target * PING_S / 3.6
            for dr in dirs:
                for _ in range(8):
                    f = lambda u: np.hypot(*(np.array(road.at(s_prev + dr * u)) - (prev.x, prev.y))) - want
                    u_hi = want
                    while f(u_hi) < 0 and 0 < s_prev + dr * u_hi < road.L:
                        u_hi *= 1.5
                    if not (0 < s_prev + dr * u_hi < road.L):
                        break
                    u_lo = 0.0
                    for _ in range(60):
                        mid = (u_lo + u_hi) / 2
                        u_lo, u_hi = (mid, u_hi) if f(mid) < 0 else (u_lo, mid)
                    lon, lat, xr, yr = F.report(*road.at(s_prev + dr * u_hi))
                    kmh = speed_kmh((prev.x, prev.y), (xr, yr), PING_S)
                    if lo < kmh < hi and float(road.dist(xr, yr)) < 5:
                        res = (lon, lat, xr, yr, kmh)
                        break
                    want += (target - kmh) * PING_S / 3.6
                if res:
                    break
            if res and role == "nearmiss_speed" and speed_kmh(res[2:4], (nxt.x, nxt.y), PING_S) > 140:
                res = None
            value = res[4] if res else None
        if res is None:
            continue
        p.lon, p.lat, p.x, p.y = float(res[0]), float(res[1]), float(res[2]), float(res[3])
        p.role, p.detail, p.value = role, detail, float(value)
        used.setdefault(tid, []).append(i)
        plan_i += 1
    if plan_i < len(plan):
        raise SystemExit(f"could only place {plan_i}/{len(plan)} noise faults")

    # 6. duplicates
    clean = [p for p in pings if p.role == "clean"]
    dup_src = rng.choice(len(clean), size=180, replace=False)
    extra: list[Ping] = []
    for j, ci in enumerate(dup_src):
        src = clean[ci]
        base = dict(driver=src.driver, ts=src.ts, trip=src.trip, k=src.k)
        if j < 120:
            q = Ping(len(pings) + len(extra), lon=src.lon, lat=src.lat, x=src.x, y=src.y, role="dup_exact", value=0.0, **base)
        else:
            tr = trip_of[src.trip]
            s_k = float(tr.s_dense[src.k * PING_S])
            nx, ny = road.normal(s_k)
            a, b = rng.uniform(-8, 8), rng.uniform(2, 10) * rng.choice([-1, 1])
            px, py = road.at(s_k)
            lon, lat, xr, yr = F.report(px + a * ny + b * nx, py - a * nx + b * ny)  # (ny,-nx) is along-road
            q = Ping(len(pings) + len(extra), lon=float(lon), lat=float(lat), x=float(xr), y=float(yr), role="dup_coord",
                     detail="variant_first" if rng.random() < 0.5 else "variant_second",
                     value=float(np.hypot(xr - src.x, yr - src.y)), **base)
            # decided rule (item 4): keep the copy closest to the road -> the original must be closer
            if not road.dist(q.x, q.y) > road.dist(src.x, src.y) + 0.5:
                raise SystemExit("dup_coord variant is not clearly farther from the road than its original")
        q.src_uid = src.uid
        extra.append(q)
    pings.extend(extra)

    # 7. receive times and late arrivals
    by_uid = {p.uid: p for p in pings}
    for p in pings:
        if p.role not in ("dup_exact", "dup_coord"):
            p.recv = p.ts + rng.uniform(0.2, 3.0)
    for q in extra:
        src = by_uid[q.src_uid]
        if q.detail == "variant_first":
            q.recv = src.recv
            src.recv = q.recv + rng.uniform(0.1, 4.0)
        else:
            q.recv = src.recv + rng.uniform(0.1, 4.0)
    dup_uids = {q.src_uid for q in extra}
    late_c = []
    for tid, lst in by_trip.items():
        for i, p in enumerate(lst[:-5]):
            if p.role == "clean" and p.uid not in dup_uids and lst[i + 1].ts - p.ts == PING_S and lst[i + 1].role == "clean":
                late_c.append(p)
    late_pick, taken = [], set()
    for li in rng.permutation(len(late_c)):
        p = late_c[li]
        if (p.trip, p.k) in taken or (p.trip, p.k + 1) in taken or (p.trip, p.k - 1) in taken:
            continue        # a delayed ping must be overtaken by its (on-time) successor
        late_pick.append(p)
        taken.add((p.trip, p.k))
        if len(late_pick) == 158:
            break
    for j, p in enumerate(late_pick):
        if j < 8:
            p.late, p.late_s = "very_late", rng.uniform(30 * 3600, 40 * 3600)
        else:
            p.late, p.late_s = "late", rng.uniform(15, 2400)
        p.recv = p.ts + p.late_s

    pings.sort(key=lambda p: (p.recv, p.uid))
    row_of = {p.uid: r for r, p in enumerate(pings)}

    # design self-check: intentionally delayed rows are exactly the out-of-order rows
    seen_max: dict[str, int] = {}
    for p in pings:
        ooo = p.ts < seen_max.get(p.driver, -1)
        if ooo != bool(p.late):
            raise SystemExit(f"out-of-order design broken at uid {p.uid}")
        seen_max[p.driver] = max(seen_max.get(p.driver, -1), p.ts)

    # 8. manifest
    man = []
    for p in pings:
        r = row_of[p.uid]
        if p.role != "clean":
            man.append((r, p.role, p.detail, p.value))
        if p.late:
            man.append((r, "late", p.late, p.late_s))
        if p.uid in edge_tags:
            t, d, v = edge_tags[p.uid]
            man.append((r, t, d, v))
        if p.uid in midnight_tags:
            t, d = midnight_tags[p.uid]
            man.append((r, t, d, np.nan))
    for tr in trips:
        lst = by_trip[tr.tid]
        for a, b, kind in tr.missing:
            after = next(p for p in lst if p.k == b)
            before = next(p for p in reversed(lst) if p.k == a - 1)
            man.append((row_of[after.uid], kind, f"{b - a} pings missing", float(after.ts - before.ts)))
    manifest = pd.DataFrame(man, columns=["row_id", "fault_type", "detail", "value"]).sort_values(["row_id", "fault_type"])

    return dict(F=F, centre=(c.x, c.y), road=road, zone=zone, trips=trips, pings=pings, by_trip=by_trip, manifest=manifest, forced_edge=forced_edge)


# --------------------------------------------------------------------------- truth (independent of src/)

def split_at_local_midnight(t0, t1, p0, p1):
    """Yield (local_day, start_xy, end_xy) pieces; split point interpolated by time."""
    a, pa = t0, np.asarray(p0, float)
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    b = ((a + LOCAL_OFFSET_S) // 86400 + 1) * 86400 - LOCAL_OFFSET_S
    while b < t1:
        pb = p0 + (p1 - p0) * (b - t0) / (t1 - t0)
        yield local_day(a), pa, pb
        a, pa, b = b, pb, b + 86400
    yield local_day(a), pa, p1


def truth_table(world) -> tuple[pd.DataFrame, dict]:
    road, zone, pings = world["road"], world["zone"], world["pings"]
    kmh = lambda a, b: np.hypot(b.x - a.x, b.y - a.y) / (b.ts - a.ts) * 3.6
    out: dict[tuple, dict] = {}
    stats = dict(segments=0, estimated=0, trip_breaks=0, checks=0)

    def cell(d, day):
        return out.setdefault((d, day), dict(truth_m=0.0, road_m=0.0, estimated_m=0.0, trip_break_road_m=0.0, n_segments=0, n_estimated=0, n_trip_breaks=0))

    for d in DRIVERS:
        mine = [p for p in pings if p.driver == d]
        received_ts = sorted({p.ts for p in mine})
        # perfect knowledge: drop injected noise and duplicate copies; keep near-misses as reported
        keep = sorted((p for p in mine if p.role in ("clean", "nearmiss_offroad", "nearmiss_speed")), key=lambda p: p.ts)
        noise = [p for p in mine if p.role.startswith("noise_")]
        assert len({p.ts for p in keep}) == len(keep)
        rts = np.array(received_ts)
        prev = None
        for p in keep:
            # self-check: every kept ping satisfies R1 and R2 against the previous kept ping
            assert road.dist(p.x, p.y) <= R1_MAX_M, ("R1", p)
            if prev is not None:
                between = rts[(rts > prev.ts) & (rts <= p.ts)]
                gaps = np.diff(np.concatenate([[prev.ts], between]))
                if gaps.max() > TRIP_BREAK_S:
                    cell(d, local_day(p.ts))["n_trip_breaks"] += 1
                    stats["trip_breaks"] += 1
                    prev = p
                    continue
                assert kmh(prev, p) <= R2_MAX_KMH, ("R2", prev, p)
                stats["checks"] += 1
                for day, a, b in split_at_local_midnight(prev.ts, p.ts, (prev.x, prev.y), (p.x, p.y)):
                    c = cell(d, day)
                    if np.any(a != b):
                        piece = LineString([a, b]).intersection(zone).length
                        c["truth_m"] += piece
                        if p.ts - prev.ts > 60:
                            c["estimated_m"] += piece
                    c["n_segments"] += 1
                    if p.ts - prev.ts > 60:
                        c["n_estimated"] += 1
                stats["segments"] += 1
                stats["estimated"] += p.ts - prev.ts > 60
            else:
                cell(d, local_day(p.ts))
            prev = p
        # self-check: every injected noise ping violates its rule against the previous kept ping
        for q in noise:
            ref = max((p for p in keep if p.ts < q.ts), key=lambda p: p.ts)
            if q.role == "noise_offroad":
                assert road.dist(q.x, q.y) > R1_MAX_M, ("noise R1", q)
            else:
                assert road.dist(q.x, q.y) <= R1_MAX_M and kmh(ref, q) > R2_MAX_KMH, ("noise R2", q)

    # physical along-road in-zone distance from the 1 s simulation (includes gaps and noise times)
    for tr in world["trips"]:
        x, y = road.at(tr.s_dense)
        coords = np.stack([np.column_stack([x[:-1], y[:-1]]), np.column_stack([x[1:], y[1:]])], axis=1)
        lengths = shapely.length(shapely.intersection(shapely.linestrings(coords), zone))
        # seconds inside an in-trip gap > 5 min: from the last ping before it to the first ping after it
        unpaid = np.zeros(len(lengths), bool)
        for a, b, kind in tr.missing:
            if kind == "gap_trip_break":
                unpaid[(a - 1) * PING_S: b * PING_S] = True
        for t, ln, u in zip(tr.t0 + np.arange(len(lengths)), lengths, unpaid):
            c = cell(tr.driver, local_day(t))
            c["road_m"] += ln
            if u:
                c["trip_break_road_m"] += ln

    rows = [dict(driver_id=d, local_day=day, truth_km=round(v["truth_m"] / 1000, 3), truth_m=round(v["truth_m"], 6),
                 road_km=round(v["road_m"] / 1000, 3), truth_minus_road_km=round((v["truth_m"] - v["road_m"]) / 1000, 3),
                 estimated_km=round(v["estimated_m"] / 1000, 3),
                 trip_break_road_km=round(v["trip_break_road_m"] / 1000, 3), n_segments=v["n_segments"], n_estimated=v["n_estimated"],
                 n_trip_breaks=v["n_trip_breaks"]) for (d, day), v in sorted(out.items())]
    return pd.DataFrame(rows), stats


# --------------------------------------------------------------------------- outputs

def sanity_plot(world, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    F, road, zone, pings = world["F"], world["road"], world["zone"], world["pings"]
    fig, ax = plt.subplots(figsize=(12, 6.5), dpi=110)
    zx, zy = zone.exterior.xy
    ax.fill(np.array(zx) / 1000, np.array(zy) / 1000, color="#4c8bf5", alpha=0.15, label="campaign zone")
    ax.plot(road.xy[:, 0] / 1000, road.xy[:, 1] / 1000, color="#555", lw=1, label="road path")
    styles = {"clean": ("#999", 2, "accepted (clean)"), "nearmiss_offroad": ("#2a9d8f", 18, "near-miss off-road (<100 m)"),
              "nearmiss_speed": ("#76c893", 18, "near-miss speed (<150 km/h)"), "noise_offroad": ("#e63946", 18, "noise: off-road"),
              "noise_speed": ("#f4a261", 18, "noise: speed spike"), "dup_coord": ("#9b5de5", 14, "dup: shifted coords")}
    for role, (col, sz, lab) in styles.items():
        sel = [p for p in pings if p.role == role]
        ax.scatter([p.x / 1000 for p in sel], [p.y / 1000 for p in sel], s=sz, c=col, label=f"{lab} (n={len(sel)})", zorder=3)
    ax.set_aspect("equal")
    ax.set_xlabel("km east of zone centroid")
    ax.set_ylabel("km north")
    ax.set_title("SYNTHETIC: generated for MUST Company hiring Quest, not real data")
    ax.legend(loc="lower left", fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(path)


def main():
    rng = np.random.default_rng(SEED)
    world = build(rng)
    pings, manifest = world["pings"], world["manifest"]
    df = pd.DataFrame({"driver_id": [p.driver for p in pings], "ts_utc": [iso(p.ts) for p in pings],
                       "lat": [f"{p.lat:.{COORD_DP}f}" for p in pings], "lon": [f"{p.lon:.{COORD_DP}f}" for p in pings]})
    with open(OUT / "pings.csv", "w", encoding="utf-8", newline="") as fh:
        df.to_csv(fh, index=False, lineterminator="\n")
    manifest.to_csv(OUT / "fault_manifest.csv", index=False, lineterminator="\n", float_format="%.3f")
    truth, stats = truth_table(world)
    truth.to_csv(OUT / "truth_distance.csv", index=False, lineterminator="\n")
    sanity_plot(world, OUT / "sanity_plot.png")

    # ---- sanity summary (printed; copied into data/README.md)
    road, zone = world["road"], world["zone"]
    crossings = len(shapely.get_parts(road.line.intersection(zone.exterior)))
    print("SYNTHETIC dataset summary")
    print(f"seed={SEED}  rows={len(df)}  drivers={df.driver_id.nunique()}  trips={len(world['trips'])}")
    print(f"road length={road.L / 1000:.3f} km  road/zone-edge crossings={crossings}")
    print(f"projection centre (zone centroid, lon/lat): {world['centre'][0]:.7f}, {world['centre'][1]:.7f}")
    print("\nfault_manifest counts:")
    shown = manifest.assign(detail=np.where(manifest.fault_type.str.startswith("gap_"), "(all lengths)", manifest.detail))
    print(shown.groupby(["fault_type", "detail"]).size().to_string())
    print(f"\ntruth self-checks: {stats['checks']} R2 checks, {stats['segments']} segments, "
          f"{stats['estimated']} estimated (>60 s), {stats['trip_breaks']} breaks >300 s (between trips + injected gap_trip_break)")
    print("\ntruth_distance.csv:")
    print(truth.to_string(index=False))
    print(f"\nTOTAL truth_km={truth.truth_m.sum() / 1000:.3f}  road_km={truth.road_km.sum():.3f}")


if __name__ == "__main__":
    main()
