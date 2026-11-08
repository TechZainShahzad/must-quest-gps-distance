# Directive: correct in-zone distance from GPS pings

> **SYNTHETIC:** the data was generated for the MUST Company hiring Quest, not real data. Nothing in this repository is confidential.
> **Submitted by Muhammad Zain Shahzad.** I set the brief, made the interpretation and ranking decisions, and reviewed the results. Claude Code assisted with implementation; [AI_LOG.md](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/AI_LOG.md) records what it produced and what I checked, changed, or rejected.
>
> **What existed before:** nothing. All code, data and documents were created new for this Quest on 4 Nov 2026; no earlier work was reused.

Repository: <https://github.com/TechZainShahzad/must-quest-gps-distance>

## Objective

Compute the km each driver drives **inside the campaign zone per local day (UTC+9)** from 10-second GPS pings, following rules R1–R6. Replace a reference calculation that overbills advertisers by **+170.3%** and underpays some drivers on specific days. Lock the corrected behaviour in with a regression check.

## Scope

**In scope:**
- a synthetic dataset with injected faults and an independent truth
- the buggy "before" reference
- a QA tool and its verification against the fault manifest
- the root cause of the chosen problem (P1)
- a corrected pure-function implementation that fixes all seven problems found
- a regression suite that fails on the old code and passes on the new

**Out of scope** (see "Still not solved"):
- road snapping / map matching
- restating reports that were already issued
- real data, storage and streaming

## Requirements

**Rules (from the Quest):**

| # | Rule |
|---|------|
| R1 | A ping more than 100 m off the road path is noise and is ignored. |
| R2 | A ping implying a speed above 150 km/h is noise and is ignored. |
| R3 | Duplicate pings (same driver, same timestamp) count once. |
| R4 | Late or out-of-order pings are placed by their own timestamp. |
| R5 | Timestamps are UTC; the daily report is cut at 00:00 local time (UTC+9). |
| R6 | Distance counts only while the vehicle is inside the zone. |

**Assumptions I confirmed at CHECKPOINT 1 (full text in [docs/assumptions.md](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/docs/assumptions.md)):**

1. R2 speed is measured against the previous **accepted** ping.
2. The R2 reference resets only at a trip break.
3. R1 runs first (stateless), then R2 runs sequentially against the last accepted ping.
4. **(I changed this)** For same-timestamp duplicates with different coordinates, keep the copy closest to the road; ties go to the first received.
5. Deduplicate, then sort by `(driver, ts)`.
6. A segment across 00:00 UTC+9 is split proportionally by time.
7. Segments are clipped to the zone polygon; only the inside length counts.
8. Segments are straight lines in a local metric projection (a limitation).
9. **(I changed this)** A straight line across a gap is counted if its implied speed is ≤ 150 km/h. Only a gap of more than 5 minutes ends the trip (not counted). Gaps over 60 s are flagged "estimated distance". Rejected noise pings don't end a trip.
10. Compare in unrounded metres, tolerance ±1 m; report km with 3 decimals.
11. **(I accepted this on a condition)** Truth = the rules applied with perfect knowledge of the faults, computed by separate code in the generator that does not import the fix. The along-road km column is kept.
12. One local AEQD projection, centred on the zone centroid.
13. The thresholds are strict (> 100 m, > 150 km/h). A day is `[00:00, 24:00)` local, so 15:00:00Z starts the new day.
14. Out-of-zone pings remain segment endpoints.
15. Split at midnight first, then clip.
16. Batch over the complete file.

## Completion criteria (measurable) and result

| Criterion | Result | Evidence |
|-----------|--------|----------|
| Fixed km = truth km ± 1 m for every driver/day | **Met: 18/18 driver-days**; max \|fixed − truth\| ≈ 4×10⁻⁷ m | [reports/before_after.csv](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/reports/before_after.csv) |
| The regression suite fails 100% of its old-behaviour cases on the buggy version | **Met: 12/12** (strict xfail, `AssertionError` only) | [reports/test-summary.md](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/reports/test-summary.md) |
| The regression suite passes 100% on the fixed version | **Met: 18/18** (plus 2 non-parametrised checks) | [reports/test-results.xml](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/reports/test-results.xml) |
| Each required area (R1/R2, R3, R4, R5, R6) has a fixture that fails on old and passes on new | **Met** (coverage table) | [reports/test-summary.md](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/reports/test-summary.md) |
| Breaking any rule in the fix makes the suite fail | **Met: 8/8 mutants caught** (sorting, dedup, zone clipping, R1, R2, trip break, UTC+9, truth importing `src`), sources restored and md5-verified | [tests/mutation_check.py](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/tests/mutation_check.py) |
| The QA tool finds the injected faults without reading the manifest | **Met: precision = recall = 1.000** for every fault type; 0/32 near-misses wrongly rejected | [reports/verification.md](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/reports/verification.md) |
| The truth is independent of the fix | **Met:** `test_generator_does_not_import_src` passes, and fails when the generator imports `src` (mutant) | [tests/test_regression.py](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/tests/test_regression.py) |
| Reproducible project run | **Verified locally:** `.\run.ps1 all` completed successfully; clean-clone setup is documented but was not separately tested | this file, "How to run" |

**What the ±1 m match does and doesn't prove:** truth is computed from the known injected faults (the same information as the fault manifest), while the fix must detect faults from the data alone. So the ±1 m match verifies the detection, ordering, day-cut and clipping logic. How realistic the straight-line geometry is gets measured separately, by the along-road column (`road_km`, `truth_minus_road_km`).

## Before / after (totals)

| | km |
|---|---:|
| Buggy (`reference_buggy`) | 925.741 |
| Fixed (`distance_fixed`) | 342.459 |
| Truth (independent) | 342.459 |

**Waterfall in my ranking order** (cumulative), from [reports/waterfall_ranked.csv](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/reports/waterfall_ranked.csv):

| Step | km |
|------|---:|
| buggy | 925.741 |
| + P1 sort | 520.995 |
| + P5 local day | 520.995 |
| + P3 noise | 462.065 |
| + P4 clip | 405.528 |
| + P2 trip breaks | 342.743 |
| + P6 duplicates | 342.560 |
| + P7 projection | 342.459 = fixed = truth |

Per driver-day values and the fix-alone table are in the report.

## Still not solved

- **Gap distance is a straight-line estimate.** It can include or miss in-zone distance where the road bends across the zone edge during the gap. Next step: estimate gaps along the road path.
- **Cost of the > 5-minute trip-break rule.** It leaves **5.925 km** of real in-zone driving unpaid (1.71% of the along-road km):
  - D05 2026-10-13: 3.663 km (that day is −4.952 km against the along-road distance)
  - D06 2026-10-14: 1.573 km
  - D02 2026-10-12: 0.689 km
  - The right threshold is a **business decision**.
- **Late pings after a day's report has been issued.** They are placed in their own past day, but no restatement is produced (8 pings arrive 30–40 h late).
- **A bad first accepted ping** within 100 m of the road can make later good pings look too fast and get them rejected. R2 can't check the first ping of a trip.
- **Straight-line geometry in general:** truth − along-road distance ranges from −4.952 to +0.977 km per driver-day.
- **Testing limits:** this is synthetic data only. The perfect QA score shows the tool applies the rules consistently at the thresholds. It doesn't show the rules catch noise of a kind the generator never produced.

## How to run

```powershell
git clone https://github.com/TechZainShahzad/must-quest-gps-distance
cd must-quest-gps-distance
python -m venv .venv
.\run.ps1 setup
.\run.ps1 all        # data -> qa -> verify -> evidence -> test (+ mutation check)
```

- On macOS/Linux: `python -m venv .venv && . .venv/bin/activate && make setup && make all`.
- Python 3.11+ is needed; no API keys or network access, except `pip install`.
- Open `reports/report.html` and `reports/map.html` locally. GitHub shows HTML files as source.
- The map loads Leaflet from a CDN and has no basemap on purpose, because the coordinates are invented.

## Actual effort

From [EFFORT.md](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/EFFORT.md):

- Sessions 1–4 (build, with checkpoint decisions in between): 1 h 21 min.
- Session 5 (my own review of outputs, CHECKPOINT 3 review and step 11 preparation): 0 h 50 min.
- **Total: 2 h 11 min** (sessions 1–5). The Loom recording and submission come after the push and are not included.

## Appendix: results and handoff links

| What | Link |
|------|------|
| **QA tool** | [qa_check.py](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/qa_check.py) · [src/ping_rules.py](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/src/ping_rules.py) · [verify_tool.py](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/verify_tool.py) |
| **Dataset (SYNTHETIC)** | [data/README.md](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/data/README.md) (label, files, fault types) · [data/pings.csv](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/data/pings.csv) · [data/generate_data.py](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/data/generate_data.py) · [data/truth_distance.csv](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/data/truth_distance.csv) · [data/fault_manifest.csv](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/data/fault_manifest.csv) |
| **Before (buggy)** | [src/reference_buggy.py](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/src/reference_buggy.py) |
| **Fix** | [src/distance_fixed.py](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/src/distance_fixed.py) · [src/geo.py](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/src/geo.py) |
| **Regression check** | [tests/test_regression.py](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/tests/test_regression.py) · [tests/conftest.py](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/tests/conftest.py) · [tests/mutation_check.py](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/tests/mutation_check.py) |
| **Test results** | [reports/test-summary.md](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/reports/test-summary.md) · [reports/test-results.xml](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/reports/test-results.xml) |
| **Results** | [reports/before_after.csv](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/reports/before_after.csv) · [reports/waterfall_ranked.csv](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/reports/waterfall_ranked.csv) · [reports/verification.md](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/reports/verification.md) · [reports/report.html](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/reports/report.html) · [reports/map.html](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/reports/map.html) |
| **Analysis** | [intent.md](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/intent.md) · [docs/findings.md](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/docs/findings.md) · [docs/assumptions.md](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/docs/assumptions.md) |
| **AI use** | [AI_LOG.md](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/AI_LOG.md) · [docs/ai/claude-code-brief.md](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/docs/ai/claude-code-brief.md) (the brief I gave Claude Code) |
| **Effort** | [EFFORT.md](https://github.com/TechZainShahzad/must-quest-gps-distance/blob/main/EFFORT.md) |
