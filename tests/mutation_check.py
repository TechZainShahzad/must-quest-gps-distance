"""Mutation check: break one rule at a time in the source, run the suite, restore the source.

SYNTHETIC: MUST Company hiring Quest. Each mutant is an exact text edit of one file. The
original bytes are always restored (try/finally) and verified by md5 before the next mutant.
A mutant is "caught" if at least one test fails.

    python tests/mutation_check.py            # writes reports/mutation-results.json
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (id, rule, file, original text, mutated text, pytest -k selection)
MUTANTS = [
    ("sorting", "R4 sort by timestamp", "src/distance_fixed.py",
     '    acc = acc.sort_values(["driver_id", "t", "row_id"])',
     "    acc = acc  # MUTANT: segments in file order", "distance_fixed"),
    ("deduplication", "R3 duplicates", "src/distance_fixed.py",
     'acc = v[v.status == "accepted"].assign(',
     'acc = v[v.status != "rejected"].assign(', "distance_fixed"),
    ("zone clipping", "R6 zone boundary", "src/distance_fixed.py",
     "inside = seg.intersection(zone_xy).length if seg.length > 0 else 0.0",
     "inside = seg.length  # MUTANT: whole segment counted", "distance_fixed"),
    ("R1 limit", "R1 off-road", "src/ping_rules.py", "R1_MAX_M = 100.0", "R1_MAX_M = 1e9", "distance_fixed"),
    ("R2 limit", "R2 speed", "src/ping_rules.py", "R2_MAX_KMH = 150.0", "R2_MAX_KMH = 1e9", "distance_fixed"),
    ("trip break", "assumption 9 (> 5 min)", "src/ping_rules.py", "TRIP_BREAK_S = 300", "TRIP_BREAK_S = 10**9", "distance_fixed"),
    ("UTC+9 offset", "R5 local day", "src/geo.py", "LOCAL_OFFSET_S = 9 * 3600", "LOCAL_OFFSET_S = 0", "distance_fixed"),
    ("truth imports src", "truth independence (CHECKPOINT 1)", "data/generate_data.py",
     "from shapely.geometry import LineString, Polygon\n",
     "from shapely.geometry import LineString, Polygon\nfrom src import geo  # MUTANT\n", "test_generator_does_not_import_src"),
]


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def run_pytest(selection: str):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider", "-k", selection],
                       cwd=ROOT, capture_output=True, text=True, env=env)
    failed = sorted({re.sub(r"\[distance_fixed\]$", "", m.split("::")[-1].split(" ")[0])
                     for m in re.findall(r"^FAILED (\S+)", r.stdout, re.M)})
    tail = [l for l in r.stdout.splitlines() if re.search(r"\d+ (passed|failed)", l)]
    return r.returncode, failed, (tail[-1].strip("= ") if tail else r.stdout[-300:])


def main() -> int:
    results = []
    base_code, base_failed, base_tail = run_pytest("distance_fixed or test_generator")
    if base_code != 0:
        print("baseline is not green; refusing to run mutants:", base_tail)
        return 2
    for mid, rule, rel, old, new, sel in MUTANTS:
        path = ROOT / rel
        original = path.read_bytes()
        before = md5(path)
        text = original.decode("utf-8")
        if text.count(old) != 1:
            raise SystemExit(f"mutant {mid}: expected exactly one match in {rel}")
        try:
            path.write_bytes(text.replace(old, new).encode("utf-8"))
            code, failed, tail = run_pytest(sel)
        finally:
            path.write_bytes(original)
        restored = md5(path) == before
        results.append(dict(mutant=mid, rule=rule, file=rel, change=f"{old.strip()}  ->  {new.strip()}",
                            caught=code != 0 and bool(failed), failing_tests=failed, pytest=tail, restored=restored))
        print(f"{mid:<18} caught={code != 0 and bool(failed)!s:<5} restored={restored!s:<5} {tail}")
        if not restored:
            raise SystemExit(f"{rel} was not restored")
    after_code, _, after_tail = run_pytest("distance_fixed or test_generator")
    out = dict(baseline=base_tail, after_restore=after_tail, after_restore_green=after_code == 0, mutants=results)
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/mutation-results.json").write_text(json.dumps(out, indent=1), encoding="utf-8", newline="\n")
    print("after restore:", after_tail)
    return 0 if all(r["caught"] for r in results) and after_code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
