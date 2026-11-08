"""Score the QA tool's flags against the injected-fault ground truth.

SYNTHETIC project (MUST Company hiring Quest). This is the ONLY script that reads
data/fault_manifest.csv. It checks that qa_check.py *finds* the injected faults
(recall), that it doesn't flag things that weren't injected (precision), and that
near-miss pings the rules accept are not rejected.

    python verify_tool.py --validation reports/validation.csv --manifest data/fault_manifest.csv --out reports/
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def score(name, rule, predicted: pd.Series, actual: pd.Series, note=""):
    tp = int((predicted & actual).sum())
    fp = int((predicted & ~actual).sum())
    fn = int((~predicted & actual).sum())
    return dict(check=name, rule=rule, injected=int(actual.sum()), flagged=int(predicted.sum()), tp=tp, fp=fp, fn=fn,
                precision=tp / (tp + fp) if tp + fp else float("nan"), recall=tp / (tp + fn) if tp + fn else float("nan"),
                note=note)


def md_table(df: pd.DataFrame) -> str:
    fmt = lambda x: f"{x:.3f}" if isinstance(x, float) else str(x)
    lines = ["| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)]
    lines += ["| " + " | ".join(fmt(x) for x in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--validation", default="reports/validation.csv")
    ap.add_argument("--manifest", default="data/fault_manifest.csv")
    ap.add_argument("--out", default="reports/")
    args = ap.parse_args(argv)

    v = pd.read_csv(args.validation, keep_default_na=False, na_values=[""])
    v["noise_reason"] = v["noise_reason"].fillna("")
    v["seg_flag"] = v["seg_flag"].fillna("")
    m = pd.read_csv(args.manifest, keep_default_na=False, na_values=[""])
    tagged = lambda *types: v.row_id.isin(m.loc[m.fault_type.isin(types), "row_id"])
    tagged_d = lambda t, d: v.row_id.isin(m.loc[(m.fault_type == t) & (m.detail == d), "row_id"])

    rows = [
        score("off-road noise rejected", "R1", v.noise_reason.str.contains("off_road"), tagged("noise_offroad")),
        score("  of which just over 100 m", "R1", v.noise_reason.str.contains("off_road") & tagged_d("noise_offroad", "just_over"),
              tagged_d("noise_offroad", "just_over"), "precision n/a within subset"),
        score("speed noise rejected (speed only)", "R2", v.noise_reason == "speed", tagged("noise_speed")),
        score("  of which just over 150 km/h", "R2", (v.noise_reason == "speed") & tagged_d("noise_speed", "just_over"),
              tagged_d("noise_speed", "just_over"), "precision n/a within subset"),
        score("any noise rejected", "R1+R2", v.status == "rejected", tagged("noise_offroad", "noise_speed")),
        score("duplicate copies dropped", "R3", v.dup.astype(bool), tagged("dup_exact", "dup_coord")),
        score("  shifted-coord copy dropped (not the original)", "R3 / item 4", v.dup.astype(bool) & tagged("dup_coord"),
              tagged("dup_coord"), "closest-to-road rule"),
        score("late pings flagged out_of_order", "R4", v.out_of_order.astype(bool), tagged("late")),
        score("estimated gaps flagged (> 60 s)", "item 9", v.seg_flag == "estimated", tagged("gap_estimated")),
        score("trip-break gaps inside trips (> 5 min)", "item 9", (v.seg_flag == "trip_break") & tagged("gap_trip_break"),
              tagged("gap_trip_break"),
              "the tool also flags breaks between trips, which are not injected faults"),
    ]
    res = pd.DataFrame(rows)

    # things that must NOT be flagged
    neg = []
    for t, cond, what in [
        ("nearmiss_offroad", v.status != "accepted", "near-miss < 100 m wrongly rejected"),
        ("nearmiss_speed", v.status != "accepted", "near-miss < 150 km/h wrongly rejected"),
        ("gap_short", v.seg_flag == "estimated", "short gap (<= 60 s) wrongly flagged estimated"),
    ]:
        sel = tagged(t)
        neg.append(dict(check=what, injected=int(sel.sum()), wrongly_flagged=int((sel & cond).sum())))
    # accuracy checks for scenario tags
    lt = m[m.fault_type == "midnight_cross"].merge(v, on="row_id")
    exp_day = (pd.to_datetime(lt.ts_utc, utc=True) + pd.Timedelta(hours=9)).dt.strftime("%Y-%m-%d")
    neg.append(dict(check="midnight-crossing ping (incl. exactly 15:00:00Z) on the new local day", injected=len(lt),
                    wrongly_flagged=int((lt.local_day != exp_day).sum())))
    ze = m[m.fault_type.isin(["zone_edge_inside", "zone_edge_outside"])].merge(v, on="row_id")
    neg.append(dict(check="zone-edge ping inside/outside correct", injected=len(ze),
                    wrongly_flagged=int((ze.in_zone.astype(str).str.lower().eq("true") != ze.fault_type.eq("zone_edge_inside")).sum())))
    negdf = pd.DataFrame(neg)

    out = Path(args.out)
    res.to_csv(out / "verification.csv", index=False, float_format="%.3f", lineterminator="\n")
    negdf.to_csv(out / "verification_negatives.csv", index=False, lineterminator="\n")
    md = ["# Verification of the QA tool against the fault manifest", "",
          "> SYNTHETIC: MUST Company hiring Quest. Generated by "
          f"`python verify_tool.py --validation {args.validation} --manifest {args.manifest} --out {args.out}`", "",
          "## Precision and recall per fault type", "", md_table(res),
          "", "## Must not be flagged / correctness of scenario rows", "", md_table(negdf)]
    (out / "verification.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
    pd.set_option("display.width", 200)
    print(res.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print()
    print(negdf.to_string(index=False))


if __name__ == "__main__":
    main()
