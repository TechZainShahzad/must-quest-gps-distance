"""Numbers quoted in docs/findings.md. SYNTHETIC: MUST Company hiring Quest, not real data.

Reads only qa_check.py outputs and data/truth_distance.csv (not the fault manifest).
    python docs/findings_numbers.py
"""
import sys
from pathlib import Path

import pandas as pd
from shapely.geometry import LineString

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.geo import LocalFrame, load_geom  # noqa: E402

R = ROOT / "reports"
s = pd.read_csv(R / "summary.csv")
w = pd.read_csv(R / "impact_by_cause.csv")
iso = pd.read_csv(R / "impact_isolated.csv")
seg = pd.read_csv(R / "buggy_segments.csv", keep_default_na=False, na_values=[""])
seg["causes"] = seg["causes"].fillna("")
v = pd.read_csv(R / "validation.csv", keep_default_na=False, na_values=[""]).set_index("row_id")
truth = pd.read_csv(ROOT / "data/truth_distance.csv")
T = truth.truth_m.sum() / 1000
B = s.buggy_km.sum()
print(f"truth total {T:.3f} km | buggy total {B:.3f} km | overbilling {B - T:+.3f} km ({(B / T - 1) * 100:+.1f}%)")
print(f"driver-days buggy > truth: {(s.buggy_minus_truth_km > 0).sum()} of {len(s)} labels; buggy < truth:")
print(s[s.buggy_minus_truth_km < 0][["driver_id", "day", "buggy_km", "truth_km", "buggy_minus_truth_km", "buggy_minus_truth_pct"]].to_string(index=False))
print(f"UTC-date buckets with no local day of that date: "
      f"{s[s.truth_km.isna()][['driver_id', 'day', 'buggy_km']].to_dict('records')}")

cols = list(w.columns[2:9])
print("\ncumulative waterfall (total km, change, change as % of truth):")
prev = None
for c in cols:
    km = w[c].sum()
    print(f"  {c:<36} {km:9.3f}  {'' if prev is None else f'{km - prev:+9.3f}  {(km - prev) / T * 100:+6.1f}%'}")
    prev = km
print(f"  {'H_truth':<36} {T:9.3f}  {T - prev:+9.3f}")
moved = (w.G_local_day_split_at_midnight - w.F_no_distance_across_trip_breaks).abs().sum() / 2
print(f"R5 step F->G: km attributed to the wrong driver-day = sum|change|/2 = {moved:.3f} km ({moved / T * 100:.1f}% of truth)")
fg = w.assign(change=w.G_local_day_split_at_midnight - w.F_no_distance_across_trip_breaks)
print(fg[fg.change.abs() > 5][["driver_id", "day", "F_no_distance_across_trip_breaks", "G_local_day_split_at_midnight", "change"]].round(3).to_string(index=False))
d = (w.G_local_day_split_at_midnight - w.truth_km) * 1000
print(f"step G vs truth per driver-day: max |diff| {d.abs().max():.1f} m (metric residual)")

print("\neach problem fixed alone (vs buggy total):")
print(iso.assign(pct_of_truth=(iso.total_change_km / T * 100).round(1)).to_string(index=False))

print("\ncounted buggy segments by cause (a segment can have several):")
c = seg.causes.str.split("; ").explode()
c = c.str.replace(r"spans \d+ min gap", "spans > 5 min gap", regex=True).str.replace(r" \(.*\)", "", regex=True)
print(c[c != ""].value_counts().to_string())
print(f"counted buggy segments: {len(seg)}; with no problem flagged: {(seg.causes == '').sum()}")

zone_ll = load_geom(ROOT / "data/zone.geojson")
F = LocalFrame(zone_ll)
zxy = F.geom(zone_ll)
nxt = int(v[(v.driver_id == "D05") & (v.index > 5376)].index[0])   # D05's next row after the noise ping, in file order
for a, b in [(749, 750), (5374, 5376), (5376, nxt), (7254, 7255), (7255, 7256), (7254, 7256)]:
    pa, pb = v.loc[a], v.loc[b]
    (xa, xb), (ya, yb) = F.xy([pa.lon, pb.lon], [pa.lat, pb.lat])
    ln = LineString([(xa, ya), (xb, yb)])
    sg = seg[(seg.from_row == a) & (seg.to_row == b)]
    print(f"segment {a}->{b} ({pa.driver_id}): straight {ln.length:.1f} m, inside zone {ln.intersection(zxy).length:.1f} m, "
          f"buggy counted {sg.km.iloc[0] * 1000 if len(sg) else 0:.1f} m | {pa.ts_utc} {pa.status} -> {pb.ts_utc} {pb.status} ({pb.reason})")
late = v.loc[7669]
print(f"row 7669: ts {late.ts_utc}, out_of_order={late.out_of_order}, received after row 6781 ({v.loc[6781].ts_utc}) -> "
      f"{(pd.Timestamp(v.loc[6781].ts_utc) - pd.Timestamp(late.ts_utc)).total_seconds() / 3600:.1f} h behind")
print(f"estimated (>60 s) truth km total: {truth.estimated_km.sum():.3f}; truth - along-road total: {truth.truth_minus_road_km.sum():+.3f} km; "
      f"range per driver-day {truth.truth_minus_road_km.min():+.3f} .. {truth.truth_minus_road_km.max():+.3f} km")
dups = v[v.status == "duplicate"]
print(f"duplicates: {len(dups)} rows; exact (spread 0): {(dups.dup_spread_m == 0).sum()}; shifted: {(dups.dup_spread_m > 0).sum()} "
      f"(spread {dups.dup_spread_m[dups.dup_spread_m > 0].min():.2f}-{dups.dup_spread_m.max():.2f} m)")

# R6 in both directions, on clean time-ordered segments (accepted pings, within trips)
import shapely  # noqa: E402
import numpy as np  # noqa: E402
acc = v[v.status == "accepted"].assign(_t=pd.to_datetime(v[v.status == "accepted"].ts_utc, utc=True)).sort_values(["driver_id", "_t"])
over = under = 0.0
n_under = 0
for _, g in acc.groupby("driver_id"):
    x, y = F.xy(g.lon, g.lat)
    cont = ~g.seg_flag.isin(["first", "trip_break"]).values[1:]
    lines = shapely.linestrings(np.stack([np.column_stack([x[:-1], y[:-1]]), np.column_stack([x[1:], y[1:]])], axis=1))[cont]
    full, inside = shapely.length(lines), shapely.length(shapely.intersection(lines, zxy))
    either = (g.in_zone.values[:-1] | g.in_zone.values[1:])[cont]
    counted = np.where(either, full, 0.0)
    over += np.clip(counted - inside, 0, None).sum()
    under += np.clip(inside - counted, 0, None).sum()
    n_under += int(((inside - counted) > 0.01).sum())
print(f"R6 either-end rule on clean segments: overcount {over / 1000:.3f} km, undercount {under / 1000:.3f} km "
      f"({n_under} segments with both ends outside that cross the zone)")

# P1 worked example (root cause): D06 late row 7669 in file order vs in timestamp order
print("\nP1 worked example, D06 rows in FILE order around row 7669:")
d6 = v[v.driver_id == "D06"]
pos = list(d6.index).index(7669)
fo = d6.iloc[pos - 2: pos + 2]
print(fo[["ts_utc", "lat", "lon", "status", "out_of_order", "in_zone", "local_day", "utc_day"]].to_string())
from src import reference_buggy as rb  # noqa: E402
for a_, b_ in zip(fo.index[:-1], fo.index[1:]):
    pa, pb = v.loc[a_], v.loc[b_]
    km = rb.haversine_km(pa.lat, pa.lon, pb.lat, pb.lon)
    print(f"  buggy segment {a_}->{b_}: haversine {km:.4f} km, counted={bool(pa.in_zone or pb.in_zone)}, day label {pb.ts_utc[:10]}")
ts = pd.to_datetime(d6.ts_utc, utc=True)
acc6 = d6[d6.status == "accepted"].assign(_t=ts).sort_values("_t")
k = list(acc6.index).index(7669)
nb = acc6.iloc[k - 1: k + 2]
print("D06 rows in TIMESTAMP order around row 7669 (accepted only):")
print(nb[["ts_utc", "lat", "lon", "in_zone", "local_day", "seg_flag"]].to_string())
fsg = pd.read_csv(R / "fixed_segments.csv")
print(fsg[(fsg.to_row.isin(nb.index[1:])) & (fsg.from_row.isin(nb.index[:-1]))].round(3).to_string(index=False))

# cost of the > 5-minute trip-break rule (assumption 9): along-road in-zone km driven inside such gaps
tb = truth[truth.trip_break_road_km > 0][["driver_id", "local_day", "trip_break_road_km", "truth_minus_road_km"]]
print("\nunpaid along-road in-zone km during in-trip gaps > 5 min, per driver-day:")
print(tb.to_string(index=False))
print(f"total unpaid {truth.trip_break_road_km.sum():.3f} km = {truth.trip_break_road_km.sum() / truth.road_km.sum() * 100:.2f}% "
      f"of along-road {truth.road_km.sum():.3f} km")
