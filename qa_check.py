"""QA tool for the GPS ping distance calculation.

SYNTHETIC project (MUST Company hiring Quest). Never reads data/fault_manifest.csv;
scoring against the manifest is a separate step (verify_tool.py).

    python qa_check.py --pings data/pings.csv --out reports/

Writes:
  validation.csv        every ping with flags and accept/reject reason
  summary.csv           per driver per day: buggy vs fixed vs truth km, fault counts
  impact_by_cause.csv   per driver per day: km change from correcting each problem in turn
  buggy_segments.csv    every segment the buggy version counts, with what is wrong with it
  report.html           self-contained summary page
  map.html              folium map (road, zone, pings by reason, buggy vs fixed track)
"""
from __future__ import annotations

import argparse
import html
from pathlib import Path

import folium
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString

from src import distance_fixed, reference_buggy
from src.geo import LOCAL_OFFSET_S, LocalFrame, load_geom
from src.ping_rules import TRIP_BREAK_S, classify

ROOT = Path(__file__).resolve().parent
REASON_COLOURS = {"off_road": "#d62828", "off_road+speed": "#9d0208", "speed": "#f77f00", "duplicate": "#7b2cbf"}


# --------------------------------------------------------------------------- diagnostics

def haversine_km_vec(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * reference_buggy.EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def naive_variant(v: pd.DataFrame, zone_xy, *, clip=False, breaks=None, local=False, metric="haversine") -> pd.DataFrame:
    """Buggy-style sum over consecutive rows of `v` (in the given row order), optionally
    with problems corrected. Diagnostic only, not the fix.

    breaks: None | "gap_break" (column) | "dt" (dt > 300 s in the given order) | "trip" (trip_id changes)
    metric: "haversine" (as the buggy code) | "aeqd" (local projection; with clip=True and
            local=True this is exactly what distance_fixed computes)
    """
    rows = []
    for d, g in v.groupby("driver_id", sort=True):
        if len(g) < 2:
            continue
        a, b = g.iloc[:-1], g.iloc[1:]
        km = haversine_km_vec(a.lat.values, a.lon.values, b.lat.values, b.lon.values)
        epoch = pd.Timestamp("1970-01-01", tz="UTC")
        t0 = (pd.to_datetime(a.ts_utc, utc=True) - epoch).dt.total_seconds().values
        t1 = (pd.to_datetime(b.ts_utc, utc=True) - epoch).dt.total_seconds().values
        either = a.in_zone.values | b.in_zone.values
        if breaks == "gap_break":
            brk = b.gap_break.values
        elif breaks == "dt":
            brk = (t1 - t0) > TRIP_BREAK_S
        elif breaks == "trip":
            brk = a.trip_id.values != b.trip_id.values
        else:
            brk = np.zeros(len(km), bool)
        ax, ay, bx, by = a.x.values, a.y.values, b.x.values, b.y.values
        for i in range(len(km)):
            if brk[i] or (not clip and not either[i]):
                continue
            pa, pb = (ax[i], ay[i]), (bx[i], by[i])
            full = LineString([pa, pb]).length
            if local and t1[i] > t0[i]:
                pieces = list(distance_fixed.split_at_midnight(t0[i], t1[i], pa, pb))
            else:   # unsplit: buggy uses the end ping's UTC date; a local day uses the start's day
                day = (pd.Timestamp(t1[i], unit="s").strftime("%Y-%m-%d") if not local
                       else distance_fixed.local_day_of(t0[i]))
                pieces = [(day, np.asarray(pa), np.asarray(pb))]
            for day, qa, qb in pieces:
                seg = LineString([qa, qb])
                share = seg.length / full if full > 0 else 0.0          # this piece's share of the segment
                if clip:
                    inside = seg.intersection(zone_xy).length if seg.length > 0 else 0.0
                    val = inside / 1000 if metric == "aeqd" else (km[i] * inside / full if full > 0 else 0.0)
                else:
                    val = seg.length / 1000 if metric == "aeqd" else km[i] * share
                if val:
                    rows.append((d, day, val))
    out = pd.DataFrame(rows, columns=["driver_id", "day", "km"])
    return out.groupby(["driver_id", "day"], as_index=False)["km"].sum()


def impact_by_cause(v: pd.DataFrame, zone_xy, truth: pd.DataFrame | None, buggy: pd.DataFrame):
    """Correct one problem at a time, cumulatively, and record the km each step removes."""
    v = v.copy()
    raw = v
    dedup = raw[raw.status != "duplicate"]
    srt = dedup.assign(_t=pd.to_datetime(dedup.ts_utc, utc=True)).sort_values(["driver_id", "_t", "row_id"]).drop(columns="_t")
    clean = srt[srt.status == "accepted"].copy()
    # a trip break = gap between consecutive received timestamps > 300 s (item 9); mark on accepted rows
    clean["gap_break"] = clean.seg_flag.eq("trip_break")
    steps = [
        ("A_buggy_as_shipped", "file order, noise, duplicates, UTC day, either-end zone", buggy),   # reference_buggy output itself
        ("B_drop_duplicates", "R3", naive_variant(dedup, zone_xy)),
        ("C_sort_by_timestamp", "R4", naive_variant(srt, zone_xy)),
        ("D_drop_noise", "R1 + R2", naive_variant(clean, zone_xy)),
        ("E_clip_to_zone", "R6", naive_variant(clean, zone_xy, clip=True)),
        ("F_no_distance_across_trip_breaks", "item 9 (> 5 min)", naive_variant(clean, zone_xy, clip=True, breaks="gap_break")),
        ("G_local_day_split_at_midnight", "R5", naive_variant(clean, zone_xy, clip=True, breaks="gap_break", local=True)),
    ]
    keys = pd.concat([s[2][["driver_id", "day"]] for s in steps]).drop_duplicates()
    if truth is not None:
        keys = pd.concat([keys, truth.rename(columns={"local_day": "day"})[["driver_id", "day"]]]).drop_duplicates()
    wide = keys.sort_values(["driver_id", "day"]).reset_index(drop=True)
    for name, _, df in steps:
        wide = wide.merge(df.rename(columns={"km": name}), on=["driver_id", "day"], how="left")
    if truth is not None:
        wide = wide.merge(truth.rename(columns={"local_day": "day"})[["driver_id", "day", "truth_km"]], on=["driver_id", "day"], how="left")
    wide = wide.fillna(0.0)
    totals = [(n, rule, df.km.sum()) for n, rule, df in steps]
    # each problem corrected ON ITS OWN on top of the buggy version (no other fix applied)
    raw_sorted = raw.assign(_t=pd.to_datetime(raw.ts_utc, utc=True)).sort_values(["driver_id", "_t", "row_id"]).drop(columns="_t")
    alone = [
        ("R3 duplicates", naive_variant(dedup, zone_xy)),
        ("R4 order by timestamp", naive_variant(raw_sorted, zone_xy)),
        ("R1+R2 noise", naive_variant(raw[raw.status != "rejected"], zone_xy)),
        ("R6 clip to zone", naive_variant(raw, zone_xy, clip=True)),
        ("item 9 trip breaks (dt > 300 s in file order)", naive_variant(raw, zone_xy, breaks="dt")),
        ("R5 local day + midnight split", naive_variant(raw, zone_xy, local=True)),
    ]
    # deltas are measured against the UNROUNDED re-sum of the buggy algorithm, so the buggy version's
    # 3-decimal rounding per driver-day (925.741 vs 925.740 km in total) does not leak into them
    base = naive_variant(raw, zone_xy)
    isolated = []
    for name, df in alone:
        j = base.merge(df, on=["driver_id", "day"], how="outer", suffixes=("_buggy", "_fix")).fillna(0.0)
        isolated.append((name, df.km.sum() - base.km.sum(), (j.km_fix - j.km_buggy).abs().sum()))
    return wide, totals, isolated


RANKED_STEPS = [   # Zain's ranking at CHECKPOINT 2; each step adds one fix to the previous one
    ("buggy", "reference_buggy as shipped"),
    ("P1_order", "R4: sort by timestamp"),
    ("P5_local_day", "R5: local day UTC+9, split at midnight"),
    ("P3_noise", "R1 + R2: drop off-road and speed noise"),
    ("P4_zone_clip", "R6: clip segments to the zone"),
    ("P2_trip_breaks", "assumption 9: no distance across a gap > 5 min"),
    ("P6_duplicates", "R3: drop duplicate copies (closest to road kept)"),
    ("P7_metric", "local AEQD projection instead of haversine (= fixed)"),
]


def ranked_waterfall(v: pd.DataFrame, zone_xy, fixed: pd.DataFrame, truth: pd.DataFrame | None, buggy: pd.DataFrame):
    """Cumulative fixes in Zain's ranking order, per driver-day and in total.
    The last step must reproduce distance_fixed exactly."""
    srt = lambda df: df.assign(_t=pd.to_datetime(df.ts_utc, utc=True)).sort_values(["driver_id", "_t", "row_id"]).drop(columns="_t")
    raw = v
    s_all = srt(raw)
    s_clean = s_all[s_all.status != "rejected"]
    s_final = s_clean[s_clean.status != "duplicate"]
    dfs = {
        "buggy": buggy,                                  # reference_buggy output itself
        "P1_order": naive_variant(s_all, zone_xy),
        "P5_local_day": naive_variant(s_all, zone_xy, local=True),
        "P3_noise": naive_variant(s_clean, zone_xy, local=True),
        "P4_zone_clip": naive_variant(s_clean, zone_xy, local=True, clip=True),
        "P2_trip_breaks": naive_variant(s_clean, zone_xy, local=True, clip=True, breaks="trip"),
        "P6_duplicates": naive_variant(s_final, zone_xy, local=True, clip=True, breaks="trip"),
        "P7_metric": naive_variant(s_final, zone_xy, local=True, clip=True, breaks="trip", metric="aeqd"),
    }
    f = fixed.rename(columns={"local_day": "day", "km": "fixed_km_display"})[["driver_id", "day", "m"]].rename(columns={"m": "fixed_m"})
    keys = pd.concat([d[["driver_id", "day"]] for d in dfs.values()] + [f[["driver_id", "day"]]]).drop_duplicates()
    if truth is not None:
        keys = pd.concat([keys, truth.rename(columns={"local_day": "day"})[["driver_id", "day"]]]).drop_duplicates()
    w = keys.sort_values(["driver_id", "day"]).reset_index(drop=True)
    for name, df in dfs.items():
        w = w.merge(df.rename(columns={"km": name}), on=["driver_id", "day"], how="left")
    w = w.merge(f, on=["driver_id", "day"], how="left")
    w["fixed_km"] = w.fixed_m / 1000
    if truth is not None:
        w = w.merge(truth.rename(columns={"local_day": "day"})[["driver_id", "day", "truth_m"]], on=["driver_id", "day"], how="left")
        w["truth_km"] = w.truth_m / 1000
    w = w.fillna(0.0)
    w["P7_vs_fixed_m"] = (w.P7_metric * 1000 - w.fixed_m)
    if truth is not None:
        w["fixed_minus_truth_m"] = w.fixed_m - w.truth_m
    return w


def buggy_segments_table(pings: pd.DataFrame, v: pd.DataFrame, zone_ll) -> pd.DataFrame:
    seg = reference_buggy.segments(pings, zone_ll)
    seg = seg[seg.counted].copy()
    a = v.set_index("row_id").loc[seg.from_row]
    b = v.set_index("row_id").loc[seg.to_row]
    seg["from_ts"], seg["to_ts"] = a.ts_utc.values, b.ts_utc.values
    dt = (pd.to_datetime(seg.to_ts, utc=True) - pd.to_datetime(seg.from_ts, utc=True)).dt.total_seconds()
    seg["dt_s"] = dt.values
    causes = []
    for (fa, fb), sa, sb, za, zb, la, lb, ooo, d in zip(
            zip(a.noise_reason.values, b.noise_reason.values), a.status.values, b.status.values, a.in_zone.values,
            b.in_zone.values, a.local_day.values, b.local_day.values, b.out_of_order.values, dt.values):
        c = []
        if fa or fb:
            c.append("noise endpoint (" + ",".join(x for x in (fa, fb) if x) + ")")
        if "duplicate" in (sa, sb):
            c.append("duplicate endpoint")
        if d < 0 or ooo:
            c.append("out of order (backwards in time)")
        elif d > TRIP_BREAK_S:
            c.append(f"spans {d / 60:.0f} min gap")
        if za != zb:
            c.append("one end outside zone, counted whole")
        if la != lb:
            c.append("crosses local midnight")
        causes.append("; ".join(c))
    seg["causes"] = causes
    seg["utc_day_vs_local_day"] = np.where(seg.day.values != b.local_day.values, "UTC day " + seg.day + " ≠ local " + b.local_day.values, "")
    return seg.sort_values("km", ascending=False)


# --------------------------------------------------------------------------- outputs

def summary_table(v, buggy, truth, fixed):
    keys = pd.concat([buggy[["driver_id", "day"]],
                      v[["driver_id", "local_day"]].rename(columns={"local_day": "day"})]).drop_duplicates()
    if truth is not None:
        keys = pd.concat([keys, truth.rename(columns={"local_day": "day"})[["driver_id", "day"]]]).drop_duplicates()
    s = keys.sort_values(["driver_id", "day"]).reset_index(drop=True)
    s = s.merge(buggy.rename(columns={"km": "buggy_km"}), on=["driver_id", "day"], how="left")
    s["fixed_km"] = np.nan
    if fixed is not None:
        s = s.drop(columns="fixed_km").merge(fixed.rename(columns={"local_day": "day", "km": "fixed_km"})[["driver_id", "day", "fixed_km"]],
                                             on=["driver_id", "day"], how="left")
    if truth is not None:
        t = truth.rename(columns={"local_day": "day"})[["driver_id", "day", "truth_km", "estimated_km", "road_km", "truth_minus_road_km"]]
        s = s.merge(t, on=["driver_id", "day"], how="left")
    s["buggy_km"] = s.buggy_km.fillna(0.0)
    s["buggy_minus_truth_km"] = (s.buggy_km - s.truth_km.fillna(0)).round(3)
    s["buggy_minus_truth_pct"] = np.where(s.truth_km > 0, (s.buggy_km / s.truth_km - 1) * 100, np.nan).round(1)
    if fixed is not None:
        s["fixed_minus_truth_km"] = (s.fixed_km - s.truth_km).round(3)
    c = v.groupby(["driver_id", "local_day"]).agg(
        n_pings=("row_id", "size"),
        rej_off_road=("noise_reason", lambda x: x.str.contains("off_road").sum()),
        rej_speed=("noise_reason", lambda x: (x == "speed").sum()),
        duplicates=("dup", "sum"),
        out_of_order=("out_of_order", "sum"),
        estimated_segs=("seg_flag", lambda x: (x == "estimated").sum()),
        trip_breaks=("seg_flag", lambda x: (x == "trip_break").sum()),
    ).reset_index().rename(columns={"local_day": "day"})
    s = s.merge(c, on=["driver_id", "day"], how="left").fillna({k: 0 for k in c.columns if k not in ("driver_id", "day")})
    for k in ["n_pings", "rej_off_road", "rej_speed", "duplicates", "out_of_order", "estimated_segs", "trip_breaks"]:
        s[k] = s[k].astype(int)
    return s


def make_map(v, road_ll, zone_ll, driver, day, path: Path):
    c = zone_ll.centroid
    m = folium.Map(location=[c.y, c.x], zoom_start=13, tiles=None, control_scale=True)
    folium.GeoJson(zone_ll.__geo_interface__, name="campaign zone",
                   style_function=lambda _: {"color": "#1d4ed8", "weight": 2, "fillOpacity": 0.08}).add_to(m)
    folium.PolyLine([(y, x) for x, y in road_ll.coords], color="#555", weight=3, name="road path").add_to(m)

    sel = v[(v.driver_id == driver) & (v.local_day == day)]
    acc = folium.FeatureGroup(name=f"accepted pings ({driver} {day})")
    for r in sel[sel.status == "accepted"].itertuples():
        folium.CircleMarker((r.lat, r.lon), radius=2, color="#2a9d8f", fill=True, weight=0, fill_opacity=0.9,
                            tooltip=f"row {r.row_id} {r.ts_utc} {r.seg_flag}").add_to(acc)
    acc.add_to(m)
    rej = v[v.status != "accepted"]
    for reason in ["off_road", "off_road+speed", "speed", "duplicate"]:
        fg = folium.FeatureGroup(name=f"rejected: {reason} (all drivers)")
        sub = rej[rej.status == "duplicate"] if reason == "duplicate" else rej[rej.noise_reason == reason]
        for r in sub.itertuples():
            folium.CircleMarker((r.lat, r.lon), radius=4, color=REASON_COLOURS[reason], fill=True, fill_opacity=0.9,
                                tooltip=f"row {r.row_id} {r.driver_id} {r.ts_utc}: {r.reason}").add_to(fg)
        fg.add_to(m)

    buggy = folium.FeatureGroup(name=f"BUGGY track: file order ({driver} {day})")
    folium.PolyLine(list(zip(sel.lat, sel.lon)), color="#e63946", weight=2, dash_array="6 4", opacity=0.8).add_to(buggy)
    buggy.add_to(m)
    fixed = folium.FeatureGroup(name=f"FIXED track: accepted, by time ({driver} {day})")
    acc_sorted = sel[sel.status == "accepted"].assign(_t=pd.to_datetime(sel.ts_utc, utc=True)).sort_values("_t")
    trip = (acc_sorted.seg_flag == "trip_break").cumsum()
    for _, g in acc_sorted.groupby(trip):
        folium.PolyLine(list(zip(g.lat, g.lon)), color="#1d4ed8", weight=3, opacity=0.8).add_to(fixed)
    fixed.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.get_root().html.add_child(folium.Element(
        '<div style="position:fixed;bottom:8px;left:8px;z-index:9999;background:#fff;padding:4px 8px;'
        'font:12px sans-serif;border:1px solid #999">SYNTHETIC: MUST Company hiring Quest, not real data. '
        'No basemap on purpose (coordinates are invented).</div>'))
    m.fit_bounds([[min(y for _, y in road_ll.coords), min(x for x, _ in road_ll.coords)],
                  [max(y for _, y in road_ll.coords), max(x for x, _ in road_ll.coords)]])
    m.save(str(path))


CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#ddd;--head:#f3f4f6;--bad:#b91c1c;--good:#15803d}
@media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#e5e5e5;--muted:#9a9a9a;--line:#333;--head:#1d1d1d;--bad:#f87171;--good:#4ade80}}
body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:24px auto;max-width:1200px;padding:0 16px}
h1{font-size:22px}h2{font-size:17px;margin-top:32px}.muted{color:var(--muted)}
.banner{border:2px solid var(--bad);padding:6px 10px;font-weight:600;display:inline-block}
table{border-collapse:collapse;font-size:12.5px;margin:8px 0;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:3px 6px;text-align:right;white-space:nowrap}
th{background:var(--head)}td.l,th.l{text-align:left}code{font-size:12px}
"""


def table_html(df: pd.DataFrame, left=("driver_id", "day", "causes", "step", "rule", "utc_day_vs_local_day", "from_ts", "to_ts")) -> str:
    def fmt(x):
        if isinstance(x, float):
            return "—" if np.isnan(x) else f"{x:,.3f}"
        return html.escape(str(x))
    head = "".join(f'<th class="{"l" if c in left else ""}">{html.escape(c)}</th>' for c in df.columns)
    body = "".join("<tr>" + "".join(f'<td class="{"l" if c in left else ""}">{fmt(x)}</td>' for c, x in zip(df.columns, r)) + "</tr>"
                   for r in df.itertuples(index=False))
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def write_report(path, args, v, summ, totals, isolated, truth, segs, metric, driver, day, fixed_ready, rw, ba):
    status = v.status.value_counts()
    reasons = v.noise_reason[v.noise_reason != ""].value_counts()
    tot_truth = truth.truth_m.sum() / 1000 if truth is not None else np.nan
    wf = pd.DataFrame([(n, r, km, km - (totals[i - 1][2] if i else km)) for i, (n, r, km) in enumerate(totals)],
                      columns=["step", "rule", "total_km", "change_km"])
    if truth is not None:
        wf.loc[len(wf)] = ["H_truth (truth_distance.csv)", "independent", tot_truth, tot_truth - totals[-1][2]]
    top = segs.head(25)[["driver_id", "from_row", "to_row", "from_ts", "to_ts", "dt_s", "km", "day", "causes", "utc_day_vs_local_day"]]
    cmd = f"python qa_check.py --pings {args.pings} --out {args.out}"
    steps = [s for s, _ in RANKED_STEPS]
    tot = [rw[s].sum() for s in steps]
    rw_total = pd.DataFrame({"step": steps, "fix": [d for _, d in RANKED_STEPS], "total_km": tot,
                             "change_km": [np.nan] + list(np.diff(tot))})
    rw_total.loc[len(rw_total)] = ["fixed (distance_fixed)", "", rw.fixed_km.sum(), np.nan]
    if truth is not None:
        rw_total.loc[len(rw_total)] = ["truth", "independent", rw.truth_km.sum(), np.nan]
    rw_days = rw[["driver_id", "day"] + steps + ["fixed_km"] + (["truth_km", "fixed_minus_truth_m"] if truth is not None else []) + ["P7_vs_fixed_m"]].copy()
    body = f"""
<h1>GPS ping distance: QA report</h1>
<p class="banner">SYNTHETIC: generated for MUST Company hiring Quest, not real data</p>
<p class="muted">Generated by <code>{html.escape(cmd)}</code>. Inputs: <code>{html.escape(str(args.pings))}</code>,
<code>{html.escape(str(args.road))}</code>, <code>{html.escape(str(args.zone))}</code>, truth <code>{html.escape(str(args.truth))}</code>.
This tool does not read <code>fault_manifest.csv</code>. Map: <a href="map.html">map.html</a> ({driver} {day}).</p>

<h2>Ping classification</h2>
<p>{len(v):,} pings: {status.get('accepted', 0):,} accepted, {status.get('rejected', 0):,} rejected as noise
({', '.join(f'{k}: {n}' for k, n in reasons.items())}), {status.get('duplicate', 0):,} duplicate copies;
{int(v.out_of_order.sum()):,} arrived out of order; {int((v.seg_flag == 'estimated').sum())} counted segments are
<b>estimated</b> (Δt &gt; 60 s); {int((v.seg_flag == 'trip_break').sum())} trip breaks (&gt; 5 min, not counted).</p>

<h2>Per driver per day (summary and fault counts)</h2>
<p class="muted">The buggy version groups by <b>UTC date</b>; truth and fixed group by <b>local day (UTC+9)</b>. Rows are aligned
on the date label, so the buggy column for a label is the UTC-date bucket. <code>fixed_km</code>:
{"from src/distance_fixed.py" if fixed_ready else "<b>pending</b>, src/distance_fixed.py is built in step 8"}.
<code>estimated_km</code> = part of truth_km from segments flagged estimated (Δt &gt; 60 s);
<code>road_km</code> = physical along-road in-zone distance (information only);
<code>truth_minus_road_km</code> shows the straight-line limitation (assumptions items 8 and 9).
Counts are what this tool flagged, by local day.</p>
{table_html(summ)}
<p>Totals: buggy {summ.buggy_km.sum():,.3f} km, truth {tot_truth:,.3f} km
(buggy − truth {summ.buggy_km.sum() - tot_truth:+,.3f} km, {(summ.buggy_km.sum() / tot_truth - 1) * 100:+.1f}%);
estimated {summ.estimated_km.sum():,.3f} km; along-road {summ.road_km.sum():,.3f} km.</p>

<h2>Before / after (driver, local day)</h2>
<p class="muted"><code>before_after.csv</code>. Buggy km is the buggy version's own UTC-date bucket for that label (rows marked
"buggy UTC-date bucket only" are dates with no local driving). Completion criterion: |fixed − truth| ≤ 1 m per driver-day,
compared on unrounded metres. Result: {int(ba.within_1m[ba.buggy_day_label_note == ""].sum())} of {int((ba.buggy_day_label_note == "").sum())} driver-days within 1 m;
max |fixed − truth| = {ba.fixed_minus_truth_m.abs().max():.6f} m.</p>
{table_html(ba)}

<h2>Waterfall in Zain's ranking order (cumulative)</h2>
<p class="muted">Each step adds one fix to the previous step (order decided at CHECKPOINT 2). The last step is computed by the
diagnostic and must equal <code>distance_fixed</code>; <code>P7_vs_fixed_m</code> shows the check. Per driver-day:
<code>waterfall_ranked.csv</code>.</p>
{table_html(rw_total)}
{table_html(rw_days)}

<h2>Each problem fixed alone</h2>
<p class="muted">Each problem corrected <b>on its own</b> on top of the buggy version. Because the problems interact,
these do not add up to the cumulative total above. <code>sum_abs_change</code> also counts km moved between days.
Changes are measured against an unrounded re-sum of the buggy algorithm, so the buggy version's own 3-decimal
rounding per driver-day does not appear as a change.</p>
{table_html(pd.DataFrame(isolated, columns=["step", "total_change_km", "sum_abs_change_per_driver_day_km"]))}

<h2>Diagnostic waterfall in the order used in docs/findings.md</h2>
<p class="muted">The order used to measure each problem at CHECKPOINT 2 (before the ranking). Per driver-day values:
<code>impact_by_cause.csv</code>. Residual G→H = metric (haversine vs local projection).</p>
{table_html(wf)}
<p class="muted">Metric check: sum over accepted, time-ordered segments (all, no zone): haversine {metric[0]:,.3f} km vs
local AEQD {metric[1]:,.3f} km (difference {(metric[0] - metric[1]) * 1000:+,.1f} m).</p>

<h2>Top 25 offending segments (counted by the buggy version, largest first)</h2>
<p class="muted">All counted buggy segments with their causes: <code>buggy_segments.csv</code>.</p>
{table_html(top)}
"""
    Path(path).write_text(f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                          f"<title>GPS QA report</title><style>{CSS}</style></head><body>{body}</body></html>", encoding="utf-8", newline="\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pings", default="data/pings.csv")
    ap.add_argument("--road", default="data/road.geojson")
    ap.add_argument("--zone", default="data/zone.geojson")
    ap.add_argument("--truth", default="data/truth_distance.csv", help="optional; omit the file to skip truth columns")
    ap.add_argument("--out", default="reports/")
    ap.add_argument("--driver", help="driver for the map (default: worst buggy − truth)")
    ap.add_argument("--day", help="local day for the map (YYYY-MM-DD)")
    args = ap.parse_args(argv)
    if "fault_manifest" in str(args.pings):
        raise SystemExit("qa_check must not read the fault manifest")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    pings = pd.read_csv(args.pings, dtype={"driver_id": str, "ts_utc": str})
    road_ll, zone_ll = load_geom(args.road), load_geom(args.zone)
    truth = pd.read_csv(args.truth) if args.truth and Path(args.truth).exists() else None

    v = classify(pings, road_ll, zone_ll)
    cols = ["row_id", "driver_id", "ts_utc", "lat", "lon", "status", "reason", "dup", "dup_group_size", "dup_spread_m",
            "dup_kept_row", "out_of_order", "off_road_m", "implied_kmh", "speed_ref_row", "noise_reason", "in_zone",
            "local_day", "utc_day", "gap_s", "seg_flag", "seg_dt_s"]
    vout = v[cols].copy()
    vout["lat"], vout["lon"] = pings["lat"].map("{:.7f}".format).values, pings["lon"].map("{:.7f}".format).values
    for c in ["dup_spread_m", "off_road_m", "implied_kmh", "gap_s", "seg_dt_s"]:
        vout[c] = vout[c].round(3)
    vout.to_csv(out / "validation.csv", index=False, lineterminator="\n")

    buggy = reference_buggy.compute(pings, road_ll, zone_ll)
    fixed = distance_fixed.compute(pings, road_ll, zone_ll, validation=v)
    summ = summary_table(v, buggy, truth, fixed)
    summ.to_csv(out / "summary.csv", index=False, lineterminator="\n")

    zone_xy = LocalFrame(zone_ll).geom(zone_ll)
    wide, totals, isolated = impact_by_cause(v, zone_xy, truth, buggy)
    pd.DataFrame(isolated, columns=["problem_fixed_alone", "total_change_km", "sum_abs_change_per_driver_day_km"]).round(3).to_csv(
        out / "impact_isolated.csv", index=False, lineterminator="\n")
    wide.round(3).to_csv(out / "impact_by_cause.csv", index=False, lineterminator="\n")

    rw = ranked_waterfall(v, zone_xy, fixed, truth, buggy)
    rw_out = rw.copy()
    rw_out.loc[len(rw_out)] = ["TOTAL", ""] + [rw[c].sum() for c in rw.columns[2:]]
    rw_out.round(6).to_csv(out / "waterfall_ranked.csv", index=False, lineterminator="\n")
    ba = summ[["driver_id", "day", "buggy_km", "fixed_km", "truth_km"]].rename(columns={"day": "local_day"}).copy()
    ba = ba.merge(rw[["driver_id", "day", "fixed_m"] + (["truth_m"] if truth is not None else [])].rename(columns={"day": "local_day"}),
                  on=["driver_id", "local_day"], how="left")
    no_local = ba.truth_km.isna() & ba.fixed_m.fillna(0).eq(0)          # buggy-only UTC-date labels
    ba[["fixed_km", "truth_km", "fixed_m", "truth_m"]] = ba[["fixed_km", "truth_km", "fixed_m", "truth_m"]].fillna(0.0)
    ba["fixed_minus_truth_m"] = ba.fixed_m - ba.get("truth_m", np.nan)
    ba["fixed_minus_truth_km"] = (ba.fixed_minus_truth_m / 1000).round(6)
    ba["within_1m"] = ba.fixed_minus_truth_m.abs() <= 1.0
    ba["buggy_day_label_note"] = np.where(no_local, "buggy UTC-date bucket only (no local driving on this date)", "")
    ba = ba[["driver_id", "local_day", "buggy_km", "fixed_km", "truth_km", "fixed_minus_truth_km", "fixed_minus_truth_m", "within_1m", "buggy_day_label_note"]]
    ba.to_csv(out / "before_after.csv", index=False, lineterminator="\n")

    distance_fixed.segments(pings, road_ll, zone_ll, validation=v).to_csv(out / "fixed_segments.csv", index=False,
                                                                         float_format="%.4f", lineterminator="\n")
    segs = buggy_segments_table(pings, v, zone_ll)
    segs.to_csv(out / "buggy_segments.csv", index=False, float_format="%.4f", lineterminator="\n")

    acc = v[v.status == "accepted"].assign(_t=pd.to_datetime(v.ts_utc, utc=True)).sort_values(["driver_id", "_t"])
    hv = ae = 0.0
    for _, g in acc.groupby("driver_id"):
        cont = ~g.seg_flag.isin(["first", "trip_break"]).values[1:]
        hv += haversine_km_vec(g.lat.values[:-1], g.lon.values[:-1], g.lat.values[1:], g.lon.values[1:])[cont].sum()
        ae += np.hypot(np.diff(g.x.values), np.diff(g.y.values))[cont].sum() / 1000

    if args.driver and args.day:
        driver, day = args.driver, args.day
    else:
        cand = summ.dropna(subset=["truth_km"]).assign(err=lambda s: (s.buggy_km - s.truth_km).abs())
        worst = cand.sort_values("err", ascending=False).iloc[0]
        driver, day = worst.driver_id, worst.day
    make_map(v, road_ll, zone_ll, driver, day, out / "map.html")
    write_report(out / "report.html", args, v, summ, totals, isolated, truth, segs, (hv, ae), driver, day, True, rw, ba)

    print(f"validation.csv: {len(v)} rows | accepted {int((v.status == 'accepted').sum())}, "
          f"rejected {int((v.status == 'rejected').sum())}, duplicate {int((v.status == 'duplicate').sum())}, "
          f"out_of_order {int(v.out_of_order.sum())}")
    print(f"buggy total {summ.buggy_km.sum():.3f} km | truth total {summ.truth_km.sum():.3f} km | map: {driver} {day}")
    for n, r, km in totals:
        print(f"  {n:<36} {r:<55} {km:10.3f} km")
    print("fixed alone (change vs buggy total, sum |change| per driver-day):")
    for n, d, a in isolated:
        print(f"  {n:<48} {d:+10.3f} km  {a:10.3f} km")
    print(f"wrote {out}/validation.csv summary.csv impact_by_cause.csv buggy_segments.csv impact_isolated.csv\n      fixed_segments.csv before_after.csv waterfall_ranked.csv report.html map.html")


if __name__ == "__main__":
    main()
