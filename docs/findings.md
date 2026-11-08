# Findings: what is wrong with the reference distance calculation

> **SYNTHETIC:** every number below is from the synthetic dataset generated for the MUST Company hiring Quest, not real data.
> Status: findings, fix, and regression checks are complete. Zain ranked the problems and chose P1 at CHECKPOINT 2; the root cause, fix result, and regression evidence follow. The full pipeline was rerun on 2026-11-08; generated outputs are in `reports/`.

## How the numbers were produced

Every number in this document comes from one of these commands, run on the current files. Re-run them in this order:

| Id | Command | Output used |
|----|---------|-------------|
| C1 | `python data/generate_data.py` | `data/*` (seed 20261104; `pings.csv` md5 `b2d55a1d544475a819b4cf5548232258`) |
| C2 | `python qa_check.py --pings data/pings.csv --out reports/` | `reports/report.html`, `validation.csv`, `summary.csv`, `impact_by_cause.csv`, `impact_isolated.csv`, `buggy_segments.csv`, `map.html` |
| C3 | `python verify_tool.py --validation reports/validation.csv --manifest data/fault_manifest.csv --out reports/` | `reports/verification.md` |
| C4 | `python docs/findings_numbers.py > reports/findings_numbers.txt` | the totals, waterfall and segment numbers quoted below |
| C5 | `python docs/make_figures.py > reports/make_figures_output.txt` | `docs/img/*.png` and the example rows quoted below |

**ASSUMPTION (impact framing):**
- The advertiser is billed, and the driver is paid, per in-zone km computed by this same calculation.
- "Overbilling" is buggy − truth for the advertiser.
- "Underpayment or wrong attribution" is km missing from, or moved to the wrong, driver-day.
- Percentages are relative to the truth total, **342.459 km** (C4).

**About the figures:** they are static renders of the same layers as `reports/map.html`, drawn from `validation.csv` (C5). `map.html` is the interactive version: open it in a browser and toggle the layers. It has no basemap on purpose, because the coordinates are invented.

## Headline

| | km | % of truth | Source |
|---|---|---|---|
| Truth (all drivers, all days) | 342.459 | | C4 |
| Buggy reference | 925.741 | | C4 |
| **Overbilling** | **+583.282** | **+170.3%** | C4 |
| Driver-days where buggy > truth | 19 of 22 date labels | | C4 |
| Driver-days where buggy < truth (driver underpaid) | D01 2026-10-13 −7.471 km (−25.9%); D04 2026-10-14 −9.422 km (−44.4%); D06 2026-10-13 −0.212 km (−1.3%) | | C4 |

**The QA tool finds the injected faults (C3).** Precision and recall are 1.000 for every fault type: off-road 56/56 (including 16/16 just over 100 m), speed 46/46 (16/16 just over 150 km/h), duplicates 180/180, late 158/158, estimated gaps 17/17 and trip-break gaps 4/4. None of the 16 + 16 near-miss pings were wrongly rejected. All 5 midnight-crossing and all 29 zone-edge pings were classified correctly.

**Caveat (stated so it isn't over-read):** the generator places its faults against the same written rules the tool applies. A perfect score shows the tool implements the rules consistently, including at the thresholds. It doesn't show that the rules catch kinds of noise the generator never produced (for example a noise ping within 100 m of the road at a plausible speed). The tool can't see those by design.

## How each problem's impact was measured

`qa_check.py` re-runs the buggy haversine sum with problems corrected **one at a time, cumulatively** (the waterfall, C2/C4):

| Step | What is corrected | Total km | Change | % of truth |
|------|-------------------|---------:|-------:|-----------:|
| A | nothing (buggy as shipped) | 925.741 | | |
| B | R3 drop duplicate copies | 925.560 | −0.181 | −0.1% |
| C | R4 sort by timestamp | 520.813 | −404.747 | −118.2% |
| D | R1 + R2 drop noise | 461.882 | −58.931 | −17.2% |
| E | R6 clip segments to the zone | 405.344 | −56.538 | −16.5% |
| F | item 9: no distance across a gap > 5 min | 342.559 | −62.785 | −18.3% |
| G | R5 local day, split at midnight | 342.560 | +0.001 | +0.0% (moves 77.279 km between days) |
| H | truth | 342.459 | −0.101 | metric residual, ≤ 9.0 m per driver-day |

**The order matters**, because the problems interact. So the table below also shows each problem fixed **on its own**, on top of the buggy version (`impact_isolated.csv`, C2/C4):

| Fixed alone | Change vs buggy | % of truth |
|-------------|----------------:|-----------:|
| R3 duplicates | −0.180 | −0.1% |
| R4 order by timestamp | −404.745 | −118.2% |
| R1+R2 noise | −58.666 | −17.1% |
| R6 clip to zone | −175.475 | −51.2% |
| item 9 trip breaks | −242.484 | −70.8% |
| R5 local day | 0.000 net; 413.802 km of per-day change | 0.0% |

R6 and item 9 look much larger when fixed alone. That's because, in file order, the late-ping jumps are themselves long segments that span gaps and leave the zone. Once the data is sorted (C), those jumps disappear. The cumulative numbers are the fair measure of each problem's *own* share.

---

## P1. Late and out-of-order pings are summed in file order (R4)

- **How it was found:**
  - C2 `validation.csv` flags 158 rows `out_of_order`.
  - In `buggy_segments.csv`, 88 counted segments are flagged "out of order (backwards in time)" (C4).
  - The waterfall step C removes the largest share of the error.
- **Evidence:**
  - D06 segment rows **6781 → 7669**: `2026-10-14T05:12:42Z` → `2026-10-12T22:57:41Z`. Buggy counts **11.691 km** for it (C5).
  - Row 7669 arrived **30.3 h** after its own timestamp (C4). It is received after pings from two days later, so the buggy code draws a line from D06's position on 10-14 back to its position on 10-12.
  - It is the largest counted buggy segment in the file.
  - Figures: `docs/img/fig_order.png` (the segment) and `docs/img/fig1_order_driver_day.png` (D06, local day 2026-10-13: the buggy file-order track zig-zags across the map; the fixed track follows the road).
- **Impact:**
  - Advertiser: **+404.747 km overbilled (+118.2%)** (cumulative step C), and the same −404.745 km when fixed alone.
  - Driver: the jumps are attributed to whichever UTC date the *late* ping's timestamp has, so km lands on the wrong days. The day effect is measured under P5.
- **Code location (for step 7):** `src/reference_buggy.py:41–47`. It iterates `groupby("driver_id")` rows in file order (`iterrows`) and never sorts by `ts_utc`.

## P2. Distance is counted across trip breaks and between trips (arises from assumption 9, not from a stated rule)

- **How it was found:**
  - In C2 `buggy_segments.csv`, 51 counted segments are flagged "spans > 5 min gap" (C4).
  - The top-25 table in `report.html` is full of 300–2,000-minute spans.
- **Evidence:**
  - D05 rows **5602 → 5759**: `2026-10-13T15:11:20Z` → `2026-10-14T00:00:25Z`, a 529-minute gap between two trips. Buggy counts **11.220 km**: a straight line from where the driver parked to where the next trip started (C5).
  - Figure: `docs/img/fig_tripbreak.png`.
- **Impact:**
  - Advertiser: **+62.785 km overbilled (+18.3%)** (cumulative step F).
  - Driver: paid for distance never driven in the zone.
- **Label: arises from assumption 9 (docs/assumptions.md), not from a stated rule (R1–R6).** This problem isn't in the brief's list for the buggy version. It falls out of "sum consecutive rows" with no trip concept. How much it costs depends on the confirmed item 9 rule (a gap > 5 min ends the trip).
- **Code location:** `src/reference_buggy.py:43–47`. Every consecutive pair is a segment, whatever the time between them.

## P3. GPS noise is not filtered (R1 off-road, R2 speed)

- **How it was found:**
  - C2 rejects 102 pings (56 off-road, 46 speed; C3 confirms 102/102 against the manifest).
  - 86 counted buggy segments have a noise endpoint (C4).
- **Evidence:**
  - D05 rows **5374 → 5376**: row 5376 (`2026-10-13T15:01:50Z`) is **511.34 m** from the road, so R1 rejects it.
  - The buggy version counts the segment into it, **2,100.5 m**, as in-zone because row 5374 is inside the zone. Only 60.1 m of that straight line is actually inside the zone (C4).
  - The way back (5376 → 5378) has both ends outside, so it isn't counted.
  - Figure: `docs/img/fig_noise.png`.
- **Impact:**
  - Advertiser: **+58.931 km overbilled (+17.2%)** (cumulative step D); −58.666 km when fixed alone.
  - Driver: overpaid by the same amount, and the per-day split is distorted.
- **Code location:** `src/reference_buggy.py:43–47`. No distance-to-road or speed check before summing.

## P4. A segment counts in full if either end is inside the zone (R6)

- **How it was found:**
  - 430 counted buggy segments have one end outside the zone and are counted whole (C4).
  - Waterfall step E.
- **Evidence:**
  - D03 rows **749 → 750** (`2026-10-11T23:48:10Z` → `23:48:20Z`, both accepted, ~74 km/h).
  - The straight line is 205.2 m, but only **11.5 m** of it is inside the zone. The buggy version counts **205.5 m** (C4, C5).
  - Figure: `docs/img/fig_zone.png`.
- **Impact:**
  - Advertiser: **+56.538 km overbilled (+16.5%)** (cumulative step E). Within trips only, the either-end rule overcounts 32.156 km (C4); the rest of the 56.5 km comes from the between-trip segments counted in P2.
  - Driver: no underpayment in this data. A segment with both ends outside that cuts through the zone would be counted as 0, but C4 found 0 such segments at the 10 s ping interval.
- **Code location:** `src/reference_buggy.py:54` (`"counted": q_inside or inside`) and `:67`.

## P5. Days are cut at UTC midnight instead of 00:00 UTC+9 (R5)

- **How it was found:**
  - `summary.csv` (C2) has buggy rows for **2026-10-11**, a date on which no local day exists. Local mornings 07:00–08:59 are 22:00–23:59 UTC on the previous date.
  - Waterfall step G, plus C4's per-day change table.
- **Evidence:**
  - Buggy buckets for 2026-10-11 (C4): D02 12.515 km, D03 24.712 km, D04 15.031 km, D06 14.609 km. That's distance driven on the morning of local 2026-10-12, invoiced to the wrong date.
  - D01: correcting R5 moves **18.415 km** from 2026-10-12 to 2026-10-13 (C4).
  - The midnight trips (for example D02 and D05, with a ping at exactly 15:00:00Z) are split at the wrong instant.
- **Impact:**
  - Advertiser: **net 0 km** over the whole period. But **77.279 km (22.6% of truth) is billed to the wrong driver-day** (C4), so every daily report is wrong.
  - Driver: this is the problem that **underpays drivers on specific days**. The final buggy numbers are below truth on D01 2026-10-13 (−7.471 km, −25.9%) and D04 2026-10-14 (−9.422 km, −44.4%), because their distance was moved to another date (C4).
- **Code location:** `src/reference_buggy.py:52` (`"day": p.ts_utc[:10]`). It takes the UTC date of the segment's end ping and doesn't split.

## P6. Duplicate pings are counted (R3)

- **How it was found:**
  - C2 flags 180 duplicate copies: 120 exact, and 60 with shifted coordinates (spread 2.33–11.25 m) (C4). C3 confirms 180/180, and confirms the shifted copy (not the original) is the one dropped in all 60 groups.
  - 129 counted buggy segments have a duplicate endpoint.
- **Evidence:**
  - D04 rows **7254 / 7255** share `2026-10-14T07:33:27Z` and are 7.95 m apart. Row 7255 is 5.99 m from the road, row 7254 is 0.00 m, so the closest-to-road rule keeps 7254.
  - Buggy path: 7254 → 7255 (8.0 m) → 7256 (178.3 m) = 186.3 m. The correct segment 7254 → 7256 is **172.9 m**, so this pair adds **+13.4 m** (C4).
  - Exact copies add a 0 m segment, so they cost nothing by themselves.
- **Impact:**
  - Advertiser: **+0.181 km (+0.1%)** (cumulative step B).
  - Driver: negligible.
  - Fixing R3 still matters for correctness: R2 speeds are computed against the kept copy.
- **Code location:** `src/reference_buggy.py:41–47`. No `(driver, ts)` deduplication.

## P7 (minor). Spherical haversine vs the local projection

- **How it was found:** the metric check in `report.html` (C2), and the H − G residual.
- **Evidence:** over all accepted, time-ordered segments (not only those in the zone), haversine with R = 6,371.0 km gives 954.386 km, against 953.982 km in the local AEQD projection: **+403.9 m (0.042%)**. After every other correction (step G), the remaining difference from truth is at most **9.0 m per driver-day** (C4).
- **Impact:** about +0.1 km on in-zone billing. That's irrelevant commercially, but it is **9× the ±1 m completion tolerance**. So the fix must use the same projection as truth (assumptions item 12), not haversine.

## Also measured (not a defect of the buggy code)

**Straight-line gap estimates (item 9, Zain's decision; see "still not solved"):**
- Truth contains **10.725 km** from segments flagged `estimated` (Δt > 60 s).
- Truth − along-road distance ranges from −4.952 to +0.977 km per driver-day, with a total of −4.996 km (C4; columns `estimated_km`, `road_km` and `truth_minus_road_km` in `report.html`).
- So the straight line can count more in-zone distance than was driven (D04 2026-10-12: +0.977 km) or less (D05 2026-10-13: −4.952 km. Of that, **3.663 km** is along-road in-zone driving during an in-trip gap of more than 5 minutes, which assumption 9 doesn't pay (`trip_break_road_km` in `data/truth_distance.csv`, from `python data/generate_data.py`); the remaining −1.289 km is the straight-line effect). Across all driver-days the rule leaves 5.925 km unpaid; see "Still not solved" in `docs/assumptions.md`.

---

## Decision at CHECKPOINT 2 (Zain)

**Ranking (Zain):** P1, P5, P3, P4, P2, P6, P7. **Problem solved first: P1.**

Zain's reasons, in his words: "P1 is the largest overbilling (+118%) and blocks every other rule. P5 is second because it is the only problem that underpays drivers and creates invoices for days nobody drove, even though the net km is zero. P3 and P4 break explicit rules R1/R2 and R6. P2 comes from my item 9 assumption rather than a written rule, so it ranks below the explicit-rule problems. P6 is an explicit rule but tiny in km. P7 is a precision issue, not a rule violation, but the fix needs it to meet ±1 m."

The corrected implementation still fixes P1–P7 (Zain's instruction), so that the regression suite can cover noise, duplicates, late pings, the UTC+9 cut and the zone boundary.

The proposal this replaced (kept for the record) is in the next section.

## Proposed ranking (AI proposal at CHECKPOINT 2, superseded by Zain's decision above)

| Rank | Problem | Why here |
|------|---------|----------|
| 1 | **P1 file order (R4)** | Largest overbilling by far (+404.7 km, +118%). It is also a **precondition** for every other rule: R2 speeds, trip gaps and the midnight split are all meaningless on unsorted data. |
| 2 | **P5 UTC day (R5)** | Net zero for the advertiser, but it is the only problem that **underpays drivers** (−44.4% on D04 2026-10-14). It puts 77.3 km on the wrong date, so every daily invoice and payout is wrong, and it creates a phantom 2026-10-11 day. It hurts trust even when totals look right. |
| 3 | **P3 noise (R1 + R2)** | +58.9 km. These are the explicitly stated rules, and R2 depends on P1 being fixed. |
| 4 | **P2 trip breaks (arises from assumption 9)** | +62.8 km, slightly more than P3. Ranked below it because its size depends on an interpretation (the 5-minute rule), not a written rule. |
| 5 | **P4 either-end zone (R6)** | +56.5 km; a simple, isolated fix (clipping). |
| 6 | **P6 duplicates (R3)** | +0.18 km; negligible money, needed for correctness. |
| 7 | **P7 metric** | +0.1 km; matters only for the ±1 m criterion. |

An **alternative ranking** puts P5 first if the business cares more about per-day driver payouts than about the total invoice.

**Recommendation for "the one to solve first":** P1. It's the largest error, and the other rules can't work correctly until it's fixed. The fixed implementation (step 8) will follow every rule anyway. The choice decides which root cause gets the deep dive in step 7, and what the Loom video leads with.

---

## Root cause (step 7)

### P1, the chosen problem: the sum follows file order, not time order

**Where:** `src/reference_buggy.py`.

```python
41    for driver, g in pings.groupby("driver_id", sort=True):
42        prev = None
43        for row_id, p in g.iterrows():          # rows in RECEIVED (file) order
...
47                km = haversine_km(q.lat, q.lon, p.lat, p.lon)   # previous ROW, not previous TIMESTAMP
```

**Why it's wrong:**
- `groupby` keeps each group in its original row order, and `pings.csv` is in received order (brief §3).
- A ping that arrives late is therefore joined to whatever row happened to arrive just before it, not to its neighbours in time.
- That creates two false segments: a jump to where the vehicle was at the late ping's timestamp, and a jump back.
- R4 says late pings are placed **by their own timestamp**. The code never reads `ts_utc` for ordering. Line 52 uses it only to take the date label.

**Worked example (real rows; `python docs/findings_numbers.py`, section "P1 worked example"):**

D06 rows **in file order** around row 7669:

| row | ts_utc | lat | lon | in_zone | out_of_order |
|----:|--------|----:|----:|:-------:|:------------:|
| 6780 | 2026-10-14T05:13:12Z | 37.506154 | 126.941174 | no | yes |
| 6781 | 2026-10-14T05:12:42Z | 37.502378 | 126.943315 | **yes** | yes |
| **7669** | **2026-10-12T22:57:41Z** | 37.504928 | 127.075806 | no | **yes** |
| 7674 | 2026-10-14T05:02:32Z | 37.497256 | 126.982568 | no | yes |

- Row 7669 has a 2026-10-12 timestamp but was received after rows from 2026-10-14: it is **30.3 h** late.
- Buggy segment **6781 → 7669**: haversine **11.6908 km**.
  - It's counted because row 6781 is inside the zone (the either-end rule, line 54).
  - It's labelled UTC day **2026-10-12** (line 52, the late ping's date).
- Buggy segment 7669 → 7674: 8.2692 km. Not counted (both ends outside), but it is the jump back.
- **Correct placement** (timestamp order, `reports/fixed_segments.csv`): row 7669 sits between D06 rows **3321** (22:57:31Z) and **3328** (22:57:51Z). That's two segments of 131.6 m each, with **0.0 m inside the zone**, on local day 2026-10-13.
- **Result:** one late ping makes the buggy version bill **11.691 km** that were never driven, all of it inside the zone by its rule, and on the wrong date. The correct contribution is **0 m**.
- Across the dataset: 158 rows arrive out of order, and 88 counted buggy segments run backwards in time (C4). Fixing only P1 removes **404.745 km** (−118.2% of truth).

**Fix (step 8):** `src/ping_rules.py` sorts each driver's deduplicated pings by `(ts_utc, row_id)` before any rule runs. `src/distance_fixed.py` builds segments only between consecutive **accepted** pings in that order.

### Brief root cause for the other problems

| Problem | Line(s) in `src/reference_buggy.py` | Why the numbers are wrong | Fixed in |
|---------|--------------------------------------|---------------------------|----------|
| P5 UTC day (R5) | 52 `"day": p.ts_utc[:10]` | Takes the UTC date of the segment's end ping. Local 07:00–08:59 is the previous UTC date, and a segment across 15:00Z is never split | `distance_fixed.split_at_midnight` (half-open local day; split by time) |
| P3 noise (R1, R2) | 43–47: every row feeds the sum | No distance-to-road or implied-speed check, so a 511 m off-road ping (row 5376) adds 2,100.5 m | `ping_rules.classify`: R1 first, then R2 against the last accepted ping |
| P4 zone (R6) | 54 `"counted": q_inside or inside`, 67 | A whole segment is counted when one end is inside (row 749 → 750: 205.5 m counted, 11.5 m inside) | `distance_fixed.segments`: `LineString.intersection(zone).length` |
| P2 trip breaks (arises from assumption 9, not a stated rule) | 43–47: no trip concept | Consecutive rows hours apart become one segment (rows 5602 → 5759: 11.220 km over 529 min) | `ping_rules.classify` assigns `trip_id` (new trip after a received gap > 300 s); segments only within a trip |
| P6 duplicates (R3) | 41–47: no `(driver, ts)` dedup | A shifted copy adds a zig-zag (rows 7254/7255: +13.4 m) | `ping_rules.classify`: keep the copy closest to the road, ties → first received |
| P7 metric | 47 `haversine_km` with R = 6,371.0 km | A spherical approximation differs from the local projection by 0.042% (≤ 9.0 m per driver-day after the other fixes) | lengths in the AEQD projection centred on the zone centroid (`src/geo.py`) |

## Result of the fix (step 8)

`python qa_check.py --pings data/pings.csv --out reports/` writes `reports/before_after.csv`:

- **Fixed = truth within 1 m on all 18 of 18 driver-days.** The largest |fixed − truth| is about 4×10⁻⁷ m.
- Fixed total **342.459 km**; buggy total 925.741 km.
- The 4 buggy-only date labels (2026-10-11) have fixed = truth = 0.

`report.html` shows the waterfall in Zain's ranking order (also in `reports/waterfall_ranked.csv`):

| Step (cumulative) | Total km |
|-------------------|---------:|
| buggy | 925.741 |
| + P1 sort by timestamp | 520.995 |
| + P5 local day, split at midnight | 520.995 |
| + P3 drop noise | 462.065 |
| + P4 clip to zone | 405.528 |
| + P2 no distance across gaps > 5 min | 342.743 |
| + P6 drop duplicate copies | 342.560 |
| + P7 local projection (= fixed) | 342.459 |
| truth | 342.459 |

- The last step matches `distance_fixed` on every driver-day to within 0.000 m (`P7_vs_fixed_m`).
- The fix-alone view is the second table in the report.
- The steps' km differ from the CHECKPOINT 2 waterfall above, because the order is different (for example, duplicates are now removed last).

## Regression check (step 9)

`.\run.ps1 test` (or `make test`) runs pytest, writes `reports/test-results.xml`, and summarises it in `reports/test-summary.md`.

- **The suite:** 18 small, hand-checkable fixtures (straight road, rectangular zone; `tests/conftest.py`), each parametrised over both implementations, plus 2 checks that aren't parametrised. Expected metres are written as arithmetic on the fixture coordinates.
- **Declared outcomes:**
  - 12 old-behaviour cases are `xfail(strict=True, raises=AssertionError)` on `reference_buggy`. They must fail with a wrong number, not a crash. If one starts passing, the suite fails.
  - 6 controls are cases where the old code happens to be right (99 m off-road, 149 km/h, adjacent exact duplicate, segment fully inside or fully outside the zone, 2-minute gap). They must pass on both.
- **Result:** 26 passed, 12 xfailed.
  - Old behaviour caught **12/12 (100%)**.
  - `distance_fixed` **18/18 (100%)**.
  - The full-dataset test confirms fixed = truth ± 1 m for all 18 driver-days.
  - `test_generator_does_not_import_src` enforces Zain's CHECKPOINT 1 condition on truth.
- **Failing-then-passing demo for the Loom:**
  - `python -m pytest --runxfail -k reference_buggy` shows the 12 old-behaviour cases as real failures (12 failed, 6 passed).
  - `python -m pytest -k distance_fixed` shows 18 passed.
- **Mutation check:** not part of the suite. It was run once from a scratch script outside the repo, breaking one rule at a time in the fixed code. Each mutant made the suite fail:

  | Mutant | `distance_fixed` tests failing |
  |--------|-------------------------------:|
  | R1 limit 100 m → 1e9 | 2 |
  | R2 limit 150 km/h → 1e9 | 3 |
  | trip break 300 s → 1e9 | 2 |
  | UTC+9 offset → 0 | 4 |

  Sorting, deduplication and clipping weren't mutated. They are covered by the R4, R3 and R6 fixtures, but no mutant was run for them.
