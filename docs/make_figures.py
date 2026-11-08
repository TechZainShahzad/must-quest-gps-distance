"""Evidence figures and examples for docs/findings.md.

SYNTHETIC: MUST Company hiring Quest, not real data. Reads only qa_check.py outputs
(reports/validation.csv, reports/buggy_segments.csv) and the road/zone files.

    python docs/make_figures.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.geo import load_geom  # noqa: E402

IMG = ROOT / "docs" / "img"
LABEL = "SYNTHETIC: MUST Company hiring Quest, not real data"


def base(ax, road, zone, title):
    zx, zy = zone.exterior.xy
    ax.fill(zx, zy, color="#4c8bf5", alpha=0.12, label="campaign zone")
    ax.plot(*road.xy, color="#777", lw=1.2, label="road path")
    ax.set_title(title, fontsize=10)
    ax.ticklabel_format(useOffset=False, style="plain")
    ax.set_xlabel("lon")
    ax.set_ylabel("lat")
    ax.text(0.01, 0.01, LABEL, transform=ax.transAxes, fontsize=7, color="#b00")


def zoom(ax, pts, pad=0.002):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    ax.set_xlim(min(xs) - pad, max(xs) + pad)
    ax.set_ylim(min(ys) - pad, max(ys) + pad)


def main():
    IMG.mkdir(parents=True, exist_ok=True)
    v = pd.read_csv(ROOT / "reports/validation.csv", keep_default_na=False, na_values=[""]).set_index("row_id")
    v["noise_reason"] = v["noise_reason"].fillna("")
    seg = pd.read_csv(ROOT / "reports/buggy_segments.csv", keep_default_na=False, na_values=[""])
    seg["causes"] = seg["causes"].fillna("")
    road, zone = load_geom(ROOT / "data/road.geojson"), load_geom(ROOT / "data/zone.geojson")

    def pick(pattern, exclude=(), max_dt=None):
        s = seg[seg.causes.str.contains(pattern, regex=False)]
        if max_dt is not None:   # ordinary segments only: normal interval, ordinary speed at both ends
            s = s[(s.dt_s > 0) & (s.dt_s <= max_dt)]
            ok = v.implied_kmh.fillna(0) <= 90
            s = s[ok.reindex(s.from_row).values & ok.reindex(s.to_row).values]
        for e in exclude:
            s = s[~s.causes.str.contains(e, regex=False)]
        return s.sort_values("km", ascending=False).iloc[0]

    from shapely.geometry import LineString
    from src.geo import LocalFrame
    F = LocalFrame(zone)
    zxy = F.geom(zone)

    def overcount_m(r):
        (xa, xb), (ya, yb) = F.xy([v.at[r.from_row, "lon"], v.at[r.to_row, "lon"]], [v.at[r.from_row, "lat"], v.at[r.to_row, "lat"]])
        ln = LineString([(xa, ya), (xb, yb)])
        return ln.length - ln.intersection(zxy).length

    zone_c = seg[(seg.causes == "one end outside zone, counted whole") & (seg.dt_s == 10)].copy()
    ok = v.implied_kmh.fillna(0) <= 90
    zone_c = zone_c[ok.reindex(zone_c.from_row).values & ok.reindex(zone_c.to_row).values]
    zone_c["over_m"] = zone_c.apply(overcount_m, axis=1)
    shifted = v.index[v.dup_spread_m.fillna(0) > 0]
    dup_c = seg[seg.from_row.isin(shifted) | seg.to_row.isin(shifted)]
    dup_c = dup_c[dup_c.causes == "duplicate endpoint"]

    ex = {
        "order": pick("out of order"),
        "noise": pick("noise endpoint", exclude=("out of order", "gap")),
        "zone": zone_c.sort_values("over_m", ascending=False).iloc[0],
        "tripbreak": pick("gap", exclude=("out of order",)),
        "dup": dup_c.sort_values("km", ascending=False).iloc[0],
    }
    print("Examples (from reports/buggy_segments.csv):")
    for k, s in ex.items():
        a, b = v.loc[s.from_row], v.loc[s.to_row]
        print(f"  {k:<9} {s.driver_id} rows {s.from_row}->{s.to_row}  {s.from_ts} -> {s.to_ts}  buggy km {s.km:.4f}  "
              f"UTC day {s.day}  | causes: {s.causes}")
        for r, p in ((s.from_row, a), (s.to_row, b)):
            print(f"      row {r}: {p.ts_utc} lat {p.lat:.7f} lon {p.lon:.7f} status={p.status} reason='{p.reason}' "
                  f"in_zone={p.in_zone} local_day={p.local_day} off_road_m={p.off_road_m:.2f}")

    # fig 1: one driver-day, buggy file-order track vs fixed track
    s = ex["order"]
    d, day = s.driver_id, v.loc[s.to_row].local_day
    sel = v[(v.driver_id == d) & (v.local_day == day)]
    acc = sel[sel.status == "accepted"].assign(_t=pd.to_datetime(sel.ts_utc, utc=True)).sort_values("_t")
    fig, ax = plt.subplots(figsize=(11, 5.2), dpi=110)
    base(ax, road, zone, f"{d}, local day {day}: buggy track (file order, all rows) vs fixed track (accepted, by time)")
    ax.plot(sel.lon, sel.lat, color="#e63946", lw=0.8, alpha=0.7, label=f"buggy: file order ({len(sel)} rows)")
    for i, (_, g) in enumerate(acc.groupby((acc.seg_flag == "trip_break").cumsum())):
        ax.plot(g.lon, g.lat, color="#1d4ed8", lw=1.6, label="fixed: accepted, sorted" if i == 0 else None)
    rej = sel[sel.status == "rejected"]
    ax.scatter(rej.lon, rej.lat, c="#f77f00", s=18, zorder=4, label=f"rejected noise ({len(rej)})")
    late = sel[sel.out_of_order.astype(str).str.lower() == "true"]
    ax.scatter(late.lon, late.lat, facecolors="none", edgecolors="k", s=40, zorder=5, label=f"out-of-order rows ({len(late)})")
    ax.legend(fontsize=7, loc="upper right")
    fig.tight_layout()
    fig.savefig(IMG / "fig1_order_driver_day.png")
    plt.close(fig)

    # figs 2-5: zoom on each example segment
    for k, title in [("noise", "noise ping kept: buggy segment to/from it"),
                     ("zone", "segment with one end outside the zone, counted whole"),
                     ("tripbreak", "segment across a long gap between trips, counted"),
                     ("order", "late ping: file-order segment jumps back in time")]:
        s = ex[k]
        a, b = v.loc[s.from_row], v.loc[s.to_row]
        nb = v[(v.driver_id == s.driver_id) & (v.status == "accepted")].assign(_t=pd.to_datetime(v.ts_utc, utc=True))
        t0, t1 = pd.to_datetime(min(a.ts_utc, b.ts_utc), utc=True), pd.to_datetime(max(a.ts_utc, b.ts_utc), utc=True)
        win = nb[(nb._t >= t0 - pd.Timedelta(minutes=1)) & (nb._t <= t1 + pd.Timedelta(minutes=1))].sort_values("_t")
        fig, ax = plt.subplots(figsize=(7, 5.5), dpi=110)
        base(ax, road, zone, f"{s.driver_id} rows {s.from_row}->{s.to_row}: {title}\nbuggy km {s.km:.3f}")
        if len(win) < 400:   # one line per trip: the fixed version counts nothing across a trip break
            for i, (_, g) in enumerate(win.groupby((win.seg_flag == "trip_break").cumsum())):
                ax.plot(g.lon, g.lat, color="#1d4ed8", lw=1.4, marker=".", ms=3, label="fixed: accepted, by time" if i == 0 else None)
        ax.plot([a.lon, b.lon], [a.lat, b.lat], color="#e63946", lw=2, ls="--", label="buggy segment")
        for r, p in ((s.from_row, a), (s.to_row, b)):
            ax.scatter([p.lon], [p.lat], s=50, zorder=5, c="#f77f00" if p.status == "rejected" else "k")
            ax.annotate(f"row {r}\n{p.ts_utc[11:19]}Z {p.status}", (p.lon, p.lat), fontsize=7, xytext=(5, 5), textcoords="offset points")
        pts = [(a.lon, a.lat), (b.lon, b.lat)]
        zoom(ax, pts, pad=0.004 if s.km < 2 else 0.01)
        ax.set_aspect(1 / 0.793)  # ~cos(37.5 deg)
        ax.legend(fontsize=7, loc="best")
        fig.tight_layout()
        fig.savefig(IMG / f"fig_{k}.png")
        plt.close(fig)
    print(f"wrote {IMG.relative_to(ROOT).as_posix()}/fig1_order_driver_day.png fig_noise.png fig_zone.png fig_tripbreak.png fig_order.png")


if __name__ == "__main__":
    main()
