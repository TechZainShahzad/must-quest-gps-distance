"""Turn the pytest JUnit XML into a short old-vs-new summary.

SYNTHETIC: MUST Company hiring Quest.
    python tests/summarize_results.py reports/test-results.xml reports/test-summary.md
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def outcome(case) -> str:
    for child in case:
        if child.tag == "skipped":
            return "xfail" if "xfail" in (child.get("type", "") + child.get("message", "")) else "skipped"
        if child.tag in ("failure", "error"):
            return "XPASS(strict)" if "XPASS" in (child.get("message") or "") else child.tag
    return "passed"


def main(xml_path: str, md_path: str) -> int:
    # the JUnit XML is committed: drop the machine's hostname (pytest has no option to omit it)
    raw = Path(xml_path).read_text(encoding="utf-8")
    Path(xml_path).write_text(re.sub(r'hostname="[^"]*"', 'hostname=""', raw), encoding="utf-8", newline="\n")
    root = ET.parse(xml_path).getroot()
    rows: dict[str, dict] = defaultdict(dict)
    for case in root.iter("testcase"):
        m = re.match(r"(.+)\[(reference_buggy|distance_fixed)\]$", case.get("name"))
        name, impl = (m.group(1), m.group(2)) if m else (case.get("name"), "—")
        rows[name][impl] = outcome(case)

    lines = ["# Regression test summary", "",
             "> SYNTHETIC: MUST Company hiring Quest. Generated from `reports/test-results.xml` by "
             "`python tests/summarize_results.py`.", "",
             "Legend: **fails (expected)** = strict xfail: the old behaviour is caught. **passes** = the test passed.", "",
             "| Test | reference_buggy (old) | distance_fixed (new) |", "|---|---|---|"]
    label = {"xfail": "fails (expected)", "passed": "passes", "failure": "**FAILED**", "error": "**ERROR**",
             "XPASS(strict)": "**passed unexpectedly**", "skipped": "skipped"}
    n_old_fail = n_old = n_new_pass = n_new = 0
    unexpected = 0
    for name, r in rows.items():
        old, new = r.get("reference_buggy"), r.get("distance_fixed")
        if old is None and new is None:
            lines.append(f"| {name} | — | {label.get(r.get('—'), r.get('—'))} (not parametrised) |")
            unexpected += r.get("—") != "passed"
            continue
        lines.append(f"| {name} | {label.get(old, old)} | {label.get(new, new)} |")
        n_old += 1
        n_new += 1
        n_old_fail += old == "xfail"
        n_new_pass += new == "passed"
        unexpected += old not in ("xfail", "passed") or new != "passed"
    expected_old_fail = sum(1 for r in rows.values() if r.get("reference_buggy") == "xfail")
    lines += ["", "## Totals", "",
              f"- Old-behaviour cases (declared `buggy_fails=True`): **{expected_old_fail}**, of which failing on reference_buggy: "
              f"**{n_old_fail}/{expected_old_fail}** ({100 * n_old_fail / max(expected_old_fail, 1):.0f}%).",
              f"- Control cases where the old code is right (must pass on both): {n_old - expected_old_fail}.",
              f"- distance_fixed: **{n_new_pass}/{n_new}** passing ({100 * n_new_pass / max(n_new, 1):.0f}%).",
              "UNEXPECTED"]
    totals_at = len(lines) - 1
    # required areas -> fixtures that fail on the old code and pass on the new code
    areas = [
        ("Noise: off-road (R1)", "test_r1_"), ("Noise: speed (R2)", "test_r2_"), ("Duplicates (R3)", "test_r3_"),
        ("Late / out-of-order (R4)", "test_r4_"), ("UTC+9 day cut (R5)", "test_r5_"), ("Zone boundary (R6)", "test_r6_"),
        ("Gaps (assumption 9)", "test_gap_"), ("All rules at scale (full dataset vs truth)", "test_full_dataset"),
    ]
    lines += ["", "## Coverage of the required areas", "",
              "Fixtures that **fail on reference_buggy and pass on distance_fixed** (from this run), and the control "
              "fixtures that pass on both.", "",
              "| Area | Fails old, passes new | Controls (pass on both) |", "|---|---|---|"]
    for area, prefix in areas:
        hit = [n for n, r in rows.items() if n.startswith(prefix)]
        caught = [n for n in hit if rows[n].get("reference_buggy") == "xfail" and rows[n].get("distance_fixed") == "passed"]
        ctrl = [n for n in hit if rows[n].get("reference_buggy") == "passed" and rows[n].get("distance_fixed") == "passed"]
        lines.append(f"| {area} | {'<br>'.join(f'`{n}`' for n in caught) or '**none**'} | {'<br>'.join(f'`{n}`' for n in ctrl) or '—'} |")
    indep = rows.get("test_generator_does_not_import_src", {}).get("—")
    lines += ["", f"Truth independence: `test_generator_does_not_import_src` **{label.get(indep, indep)}**. It fails if "
              "`data/generate_data.py` imports `src`, `distance_fixed` or `ping_rules` (proved by the mutation check below)."]

    mpath = Path(xml_path).with_name("mutation-results.json")
    if mpath.exists():
        import json
        mr = json.loads(mpath.read_text(encoding="utf-8"))
        lines += ["", "## Mutation check", "",
                  "Generated by `python tests/mutation_check.py`. Each row breaks one rule by an exact text edit of the "
                  "source, runs the relevant tests, then restores the file and verifies it by md5.", "",
                  "| Mutant | Rule | File | Caught | Failing tests | Restored |", "|---|---|---|---|---|---|"]
        for m in mr["mutants"]:
            lines.append(f"| {m['mutant']} | {m['rule']} | `{m['file']}` | {'yes' if m['caught'] else '**NO**'} | "
                         f"{'<br>'.join(f'`{t}`' for t in m['failing_tests'])} | {'yes' if m['restored'] else '**NO**'} |")
        lines += ["", f"Baseline before mutants: {mr['baseline']}. After restoring: {mr['after_restore']} "
                      f"({'green' if mr['after_restore_green'] else '**NOT green**'})."]
        dd = next((m for m in mr["mutants"] if m["mutant"] == "deduplication"), None)
        if dd and "test_r3_exact_duplicate_resent_later_counts_once" not in dd["failing_tests"]:
            lines += ["", "Note: with deduplication disabled, `test_r3_exact_duplicate_resent_later_counts_once` still passes. "
                          "After sorting, an exact copy sits next to its original and adds a 0 m segment, so only the "
                          "shifted-coordinate fixture (and the full dataset) catch that mutant."]
        unexpected += sum(not m["caught"] or not m["restored"] for m in mr["mutants"]) + (not mr["after_restore_green"])

    lines[totals_at] = f"- Unexpected outcomes (tests and mutants): **{unexpected}**."
    Path(md_path).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines[totals_at - 3: totals_at + 1]))
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
