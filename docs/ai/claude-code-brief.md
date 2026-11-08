# Brief: MUST Company Quest: GPS ping distance (find it, build the tool, fix it)

## 0. How we work on this (read first)

This is a **hiring assessment**. I (Muhammad Zain) am responsible for every judgement in it, and I have to defend it in a call.

- **You (Claude Code) build and run; I decide.** At every `CHECKPOINT` below, stop and wait for my answer. Don't continue past a checkpoint on your own.
- **Honesty rules (from the Quest):**
  - Never invent results. Every number in the docs comes from a command we actually ran, and the command is shown next to it.
  - Label all data as **SYNTHETIC**, and label every assumption as an **ASSUMPTION**.
  - `AI_LOG.md` must be truthful about **who caught what**. For each entry, record whether the AI's mistake was caught by me, by a test, or by you re-checking. Don't write entries in my voice for corrections I didn't make.
- **Keep the scope narrow.** The target is 6–10 hours of focused work. Track actual time per session in `EFFORT.md` (start/stop and what was done). I report it honestly.
- **Use Python 3.11+** with pandas, shapely, pyproj (or a haversine plus local projection), pytest and folium (for the map). Everything must run offline from a clean clone with `make all` (or a `run.ps1` / `run.sh`). No API keys and no paid tools.

## 1. The case (from the Quest, verbatim rules)

A vehicle-advertising platform records GPS pings from drivers' phones every 10 seconds. Advertisers pay per kilometre driven **inside a campaign zone**. These rules apply:

| # | Rule |
|---|------|
| R1 | A ping **more than 100 m off the road path** is GPS noise and must be ignored. |
| R2 | A ping implying a **speed above 150 km/h** is GPS noise and must be ignored. |
| R3 | **Duplicate** pings (same driver, same timestamp) count **once**. |
| R4 | **Late or out-of-order** pings are placed by **their own timestamp**. |
| R5 | Timestamps are stored in **UTC**; the daily report is cut at **00:00 local time (UTC+9)**. |
| R6 | Distance counts **only while the vehicle is inside the zone**. |

We must:
- Generate a **synthetic, clearly labelled dataset of at least 2,000 pings** containing these faults
- Write a **reference calculation that gets the distance wrong**
- Find the problems, build a tool, find the root cause, fix it, and lock it in with a regression check

## 2. Interpretation decisions (CHECKPOINT 1: propose, then wait for me)

The rules leave gaps. Write `docs/assumptions.md` with **your proposal and the alternatives** for each item below. **Stop and wait** for my decision before you write the fixed implementation.

1. **R2 speed reference:** is speed measured from the previous *accepted* ping, or the previous raw ping? (Proposal: the previous *accepted* ping. Otherwise one noise ping makes the next good ping look too fast too.)
2. **R2 after a long gap:** is a big gap treated as a reset, so the next ping isn't rejected for "speed"?
3. **R1 vs R2 order:** which filter runs first, and does the order change results?
4. **R3 duplicates with different coordinates:** keep the first, keep the closest to the road, or flag both?
5. **R4 ordering:** sort by timestamp per driver before any filter; how to handle ties after deduplication?
6. **R5 midnight crossing:** a segment that crosses 00:00 local time. Split its distance proportionally by time at the cut, or assign it to the day of its end ping?
7. **R6 zone boundary:** a segment with one end inside and one outside. Clip it to the zone polygon so only the inside length counts (proposal), or count it all or none?
8. **Segment geometry:** straight line between accepted pings, or snapped to the road path? (Proposal: straight line in a local metric projection, documented as a limitation.)
9. **Missing pings and long gaps** (e.g. more than 60 s): still count the straight-line distance, or skip it?
10. **Rounding:** report in km with 3 decimals; compare totals with a tolerance of at most 1 m.

## 3. Synthetic dataset (`data/`)

- `generate_data.py` with a **fixed random seed**. A header comment and a `README` state: *SYNTHETIC: generated for MUST Company hiring Quest, not real data*.
- **Geography:** a simple **road path** as a polyline (`road.geojson`) and a **campaign zone** polygon (`zone.geojson`) that the road enters and leaves several times.
- **Drivers and time:** 5–8 drivers over 2–3 days, with trips scheduled so several **cross 00:00 UTC+9** (that's 15:00 UTC) and several cross the **zone boundary**.
- **Output** `pings.csv` (≥ 2,000 rows) with `driver_id, ts_utc (ISO 8601 with Z), lat, lon` **in received order**, not sorted.
- **Faults injected,** each recorded in a separate ground-truth file (`fault_manifest.csv`: row id, fault type) that the tools **must not read** when detecting problems:
  - Off-road noise jumps (> 100 m from the road), some only just over and some only just under 100 m
  - Speed spikes (> 150 km/h), some only just over and some only just under 150 km/h
  - Exact duplicates, plus same-timestamp duplicates with slightly different coordinates
  - Late and out-of-order arrivals
  - Trips that cross the UTC+9 midnight
  - Pings just inside and just outside the zone edge
- **Ground truth:** because we generate the true path, also write `truth_distance.csv` with the true in-zone km **per driver per local day**. This is computed from the clean simulated track, independently of both implementations.

## 4. The wrong reference calculation (`src/reference_buggy.py`)

A realistic, naive implementation. Mark it clearly as the **"before"** version. It:
- sums the haversine distance between consecutive rows **in file order**
- doesn't remove off-road or speed noise
- counts duplicates
- groups by **UTC date**
- counts a whole segment if **either** endpoint is inside the zone

Keep it plausible, the kind of code a real team might ship.

## 5. Find the problems (CHECKPOINT 2)

Build the QA tool (section 6) first, then use it to list **at least 3 problems** in `docs/findings.md`. For each, give:
- how it was found (which tool output or check, with the exact command)
- evidence: row ids, a driver/day example, and a map screenshot
- impact on the **advertiser** (overbilling) and on the **driver** (under-payment or wrong attribution), in km and %

Then **stop**. I'll rank them and choose the one to solve first. Propose a ranking with reasons, but the decision is mine.

## 6. The QA tool (must run for someone else)

`qa_check.py` (one command, e.g. `python qa_check.py --pings data/pings.csv --out reports/`). It produces:

- **`reports/validation.csv`:** every ping with flags (`dup`, `out_of_order`, `off_road_m`, `implied_kmh`, `noise_reason`, `in_zone`, `local_day`), and the reason it was accepted or rejected.
- **`reports/report.html`:** a self-contained page showing:
  - a summary table per driver per local day: buggy km vs fixed km vs truth km, with the differences and the counts of each fault
  - the top offending segments
- **`reports/map.html`:** a folium map with the road, the zone, accepted pings, rejected pings coloured by reason, and the buggy track versus the fixed track for a selected driver and day.

Check the tool against `fault_manifest.csv` **only in a separate verification step** (`verify_tool.py`). Report precision and recall per fault type, so we know the checker finds the injected faults and doesn't just reproduce them.

## 7. Root cause (in `docs/findings.md`)

For the chosen problem (and briefly for the others), show **exactly where and why** the numbers are wrong:
- the line(s) in `reference_buggy.py`
- one worked example with real rows from the dataset: the pings, the computed segment, and the wrong km

## 8. The fix (`src/distance_fixed.py`)

A corrected reference implementation that follows every rule and every assumption I confirmed at CHECKPOINT 1. It is a pure function: input pings, road and zone; output km per driver per local day. Then produce:

- `reports/before_after.csv` and a table in the report. Columns: **driver, local day, buggy km, fixed km, truth km, fixed − truth**.
- The fixed km must match truth within tolerance for every driver/day. Where it doesn't, explain why (e.g. an assumption or a geometry limitation). Don't hide it.

## 9. Regression and verification (`tests/`)

pytest. Each test is **parametrised over both implementations**, and the expected outcome is declared: it fails on `reference_buggy` (marked xfail with `strict=True`) and passes on `distance_fixed`. Add small, hand-checkable fixtures, one per rule:

- noise: off-road at 99 m vs 101 m; speed at 149 vs 151 km/h; one noise ping followed by a good ping
- an exact duplicate, and a same-timestamp duplicate with different coordinates
- a late ping arriving after later pings
- a trip across 00:00 UTC+9 (15:00 UTC); a ping at exactly 15:00:00Z
- a segment crossing the zone edge, one entirely outside, one entirely inside
- a full-dataset check: the fixed totals equal truth within tolerance

`make test` writes `reports/test-results.xml` and a short `reports/test-summary.md` showing what fails on the old code and what passes on the new.

## 10. Deliverables (the Quest requires exactly 3 submission items)

1. **`intent.md`:** the problems found, how they were found, the ranking and why, the chosen problem and why, and what was left out.
2. **`directive.md`:** objective, scope, requirements (the rules plus confirmed assumptions), completion criteria (measurable: "fixed km = truth ± 1 m for all driver/days; the regression suite fails 100% of its old-behaviour cases on the buggy version and passes 100% on the fixed one"), and a **results/handoff appendix** with links to the tool, the dataset (with its SYNTHETIC label), the fix, the regression check and the test results. Also cover: what's still not solved, how to run it, and actual effort.
3. **Loom video (max 5 min),** recorded by me. I will explain the project and my decisions in my own words.
   - the problem (30 s)
   - running the tool and the map (60 s)
   - the root cause (45 s)
   - the fix with before/after numbers (60 s)
   - the regression run, failing then passing (45 s)
   - AI use and corrections (45 s)

Also keep `AI_LOG.md` (what AI produced, what was checked, changed or rejected, and who caught it) and `EFFORT.md`. Link them from `directive.md`.

## 11. Repository and access

- Create a **public** GitHub repo (synthetic data only, nothing confidential), e.g. `must-quest-gps-distance`. Push only when I say so. Don't push before my final review.
- README: one paragraph explaining what this is, then quick start, the folder map and the SYNTHETIC label.
- **Before submission,** open every link from `directive.md` in a logged-out browser session and confirm it loads.

## 12. Order of work, with checkpoints

1. Scaffold the repo, `EFFORT.md` and `AI_LOG.md`.
2. Write the interpretation proposals → **CHECKPOINT 1** (I decide).
3. Generate the data and the truth file, and sanity-check them (counts per fault type, a plot).
4. Write the buggy reference.
5. Build the QA tool and `verify_tool.py`.
6. Write the findings → **CHECKPOINT 2** (I rank and choose).
7. Root cause.
8. The fix, plus the before/after table.
9. Regression tests (fail old, pass new).
10. `intent.md` and `directive.md` → **CHECKPOINT 3** (I review the submission and record the Loom in my own words).
11. Final link check, then push.
