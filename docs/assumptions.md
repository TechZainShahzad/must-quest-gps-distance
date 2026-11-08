# Interpretation decisions (CHECKPOINT 1)

> **SYNTHETIC** project. Every item below is an **ASSUMPTION**: the rules don't state it. Status: **DECIDED by Zain at CHECKPOINT 1 (2026-11-04).** Zain changed items 4 and 9 and accepted items 3 and 11 with conditions. Where a decision differs from the original proposal, the item's section has a **Decision** line, and the decision is what the code follows.

The six rules (R1–R6) leave gaps. For each gap: what I propose, the alternatives, why, and what each choice does to the advertiser (overbilling) and the driver (underpayment). Items 1–10 come from the brief. Items 11–16 are extra gaps I found while planning the data and the ±1 m completion criterion. Some of them decide whether that criterion can be met at all.

## Decision sheet

| # | Question | Proposal | Zain's decision |
|---|----------|----------|-----------------|
| 1 | R2 speed reference | Previous **accepted** ping | Accepted |
| 2 | R2 after a long gap | Reset the speed anchor at a trip break (gap > G, item 9); otherwise no reset | Accepted |
| 3 | R1 vs R2 order | One pass per ping: R1, then R2 against the last accepted ping. Accept only if both pass; record every failed reason | Accepted: R1 first (stateless), then R2 sequentially vs the last accepted ping |
| 4 | R3 same-ts, different coords | Keep the **first received**, flag the group and record its spread in m | **Changed:** keep the copy closest to the road; tie → first received |
| 5 | R4 ordering / ties | Deduplicate, then stable sort by `(driver_id, ts_utc)`; ties can't survive dedup | Accepted |
| 6 | R5 midnight crossing | Split at the interpolated midnight point (= proportional by time) | Accepted |
| 7 | R6 zone boundary | Clip each segment to the polygon; count only the inside length | Accepted |
| 8 | Segment geometry | Straight line in a local metric projection (limitation documented) | Accepted |
| 9 | Gaps | Raw gap ≤ G = 60 s: count the straight line. Gap > G: trip break, no distance | **Changed:** count the straight line across a gap if implied speed ≤ 150 km/h; only a gap > 5 min ends the trip; flag gaps > 60 s as "estimated distance" |
| 10 | Rounding / tolerance | Compare **unrounded** metres, \|fixed − truth\| ≤ 1 m; round to 3-decimal km for display only | Accepted |
| 11 | What "truth" means | Rules applied with perfect fault knowledge to the clean track, with the same geometry model as item 8 | Accepted, on condition: truth is separate code in the generator that does not import `distance_fixed`; keep the along-road km column |
| 12 | Metric / projection | Local azimuthal-equidistant projection centred on the zone, used by the fixed code **and** truth | Accepted |
| 13 | Threshold inclusivity | `> 100 m` and `> 150 km/h` are strict; a day is `[00:00, 24:00)` local; 15:00:00Z belongs to the **new** day | Accepted |
| 14 | Pings outside the zone | Keep them as segment endpoints (filter segments, not pings) | Accepted |
| 15 | Midnight + zone edge in one segment | Split at midnight first, then clip each piece | Accepted |
| 16 | Batch vs streaming | Compute each report on the complete file; no "day already closed" handling | Accepted (late-after-report added to "still not solved") |

---

## 1. R2: speed reference

**Proposal (ASSUMPTION):** implied speed = straight-line distance / Δt from the **previous accepted** ping of the same driver.

**Alternatives:**
- *Previous raw ping:* one noise ping makes the next good ping look fast too, so two pings are dropped for one fault (cascade). That underpays the driver.
- *Both neighbours (spike test):* reject a ping only if it is fast **in and out**. This is more robust but isn't what R2 says, and it needs look-ahead.

**Risk with the proposal, "anchor lock":** if a bad ping were ever accepted as the anchor, every later good ping could look too fast and be rejected. This is mitigated by item 3: an anchor must pass R1 (≤ 100 m from the road), so it is never far off. Item 2 also resets the anchor at every trip break. The QA tool will report long runs of consecutive speed rejections so that any lock that does happen is visible.

## 2. R2 after a long gap

**Proposal (ASSUMPTION):** reset only at a **trip break** (a raw gap > G, see item 9). The first ping of a trip has no speed reference, so it is checked by R1 only.

**Why not reset on every "long" gap:** a long Δt *lowers* the implied speed, so a gap can't cause a false speed rejection by itself. A reset matters only to recover from anchor lock and to start new trips.

**Known limitation:** a speed spike that is the *first* ping of a trip can't be detected by R2. I'll document it and not inject that case as a "should be caught" fault.

## 3. R1 vs R2 order

**Decision (Zain):** accepted. R1 first (stateless), then R2 sequentially against the last accepted ping.

**Proposal (ASSUMPTION):** one sequential pass per driver over the sorted, deduplicated pings. For each ping, compute R1 (distance to the road) and R2 (speed vs the last accepted ping). Accept only if both pass. `noise_reason` records every failed rule (`off_road`, `speed`, `off_road+speed`).

**Does the order matter?** Not inside one ping: "accept iff both pass" is symmetric. It **does** matter if the filters run as two separate passes over the whole dataset. If R2 runs first on unfiltered data, an off-road ping can become the speed anchor and get the next good ping rejected. If R1 runs first as a full pass, R2 sees a cleaner sequence. My single pass is equivalent to "R1 first, then R2 on the survivors", which I'll show with a test.

## 4. R3: same timestamp, different coordinates

**Decision (Zain, changed):** keep the copy **closest to the road path**; on an exact tie, keep the first received. The rest of this section is the original proposal, kept for the record.

**Proposal (ASSUMPTION):** exact duplicates collapse to one row. For same-`(driver, ts)` rows with different coordinates, keep the **first received** row, mark the others `dup`, and record the group's spread in metres in `validation.csv`.

**Alternatives:**
- *Keep the one closest to the road:* better quality, but it's a heuristic that depends on R1. It also prefers the copy that happens to be nearer the road, not the original fix.
- *Average the group:* invents a position nobody reported.
- *Flag and drop all of them:* safe for the advertiser, but underpays the driver for both adjacent segments.

**Why first-received:** it's deterministic, independent of geometry, and easy to explain. It relies on a **further ASSUMPTION**: a retransmission arrives after its original. The generator will follow that, and the README will state it.

**Tolerance impact:** whichever copy is kept moves one segment endpoint by the spread (a few metres). If the kept copy isn't the one truth uses, the day total can drift by more than 1 m. So truth (item 11) must use the same rule, or the spread must be small.

## 5. R4: ordering and ties

**Proposal (ASSUMPTION):** parse `ts_utc` as UTC, deduplicate (item 4), then stable-sort by `(driver_id, ts_utc)`. After dedup there are no ties on `(driver, ts)`. A ping is flagged `out_of_order` if its timestamp is earlier than the maximum timestamp already received for that driver.

## 6. R5: midnight crossing

**Proposal (ASSUMPTION):** split the segment at local midnight. The split point is linearly interpolated by time along the straight segment (constant speed inside a 10 s segment). Each part is assigned to its own local day. This is the same as "proportional by time".

**Alternative: assign it all to the end ping's day.** Simpler, but it moves up to one segment (≤ ~420 m at 150 km/h) to the wrong day for each crossing. That's wrong attribution for the driver, and wrong per-day billing for the advertiser.

## 7. R6: zone boundary

**Proposal (ASSUMPTION):** clip each segment to the zone polygon (shapely `intersection`) and count only the inside length. A segment with both ends outside can still cross a corner of the zone, and its inside part counts too.

**Alternatives:**
- *Either end inside* (the buggy version) overbills the advertiser.
- *Both ends inside* underpays the driver.
- *Count by the start ping* is biased by the direction of travel.

## 8. Segment geometry

**Proposal (ASSUMPTION):** each segment is a straight line between consecutive accepted pings, measured in a local metric projection (item 12).

**Alternative: snap to the road (map matching).** More accurate on curves, but it's a large scope increase.

**Limitation (to be documented):** a straight line is never longer than the road between the same two points, so on curves and across rejected pings the fixed version slightly **under-counts**. This bias is in the advertiser's favour. The size of the effect will be measured, not guessed (see item 11).

## 9. Missing pings and long gaps

**Decision (Zain, changed):**
- A segment across a gap is counted as a straight line if its implied speed is ≤ 150 km/h (that is R2 applied to the ping after the gap).
- Only a gap of **more than 5 minutes (300 s)** ends the trip; that segment is not counted and the R2 anchor resets.
- Every counted segment whose Δt is **over 60 s** is flagged `estimated` in the report.
- Rejected noise pings don't end a trip.

**Implementation ASSUMPTIONS that follow from the decision:** the 5-minute break is measured on the deduplicated **received** timestamps (so rejected noise pings fill the gap); the 60 s "estimated" flag is measured on Δt between the two **accepted** endpoints of the counted segment.

The rest of this section is the original proposal, kept for the record.

**Proposal (ASSUMPTION):** a **trip** is a run of deduplicated **raw** pings in which no consecutive gap exceeds G = 60 s (5 missed pings).
- Inside a trip, a segment between accepted pings counts as a straight line, even if noise pings were rejected in between. Rejections don't break a trip, because the vehicle was still sending.
- Across a gap > G, no distance is counted (the app was off or the vehicle parked), and the R2 anchor resets.

**Alternatives:**
- *Always count the straight line:* across a 30-minute gap the chord can run through the zone when the driver wasn't there, or skip a loop.
- *G = 300 s:* more tolerant of tunnels, but bridges real stops.

**Impact:** a smaller G underpays drivers who have patchy signal. A larger G risks billing straight lines that were never driven.

## 10. Rounding and tolerance

**Proposal (ASSUMPTION):** compute in metres as float64. Compare **unrounded** values: |fixed − truth| ≤ 1.0 m per driver per local day. Round to 3-decimal km only in reports.

**Why:** rounding both sides to 0.001 km first adds up to ±0.5 m of error on each side. A real 0.4 m difference could then show as 1 m and fail, or a 1.4 m difference could show as 1 m and pass.

## 11. What "truth" means (extra; this decides whether ±1 m is possible)

**Decision (Zain):** accepted on condition that truth is computed by **separate code in the generator that does not import `distance_fixed`**. Keep the along-road km column.

The brief says truth comes "from the clean simulated track, independently of both implementations". Two readings:

- **(a) Proposal (ASSUMPTION):** the generator keeps the clean, pre-fault positions of every true ping. It applies the rules with *perfect knowledge*: it drops the injected noise it knows about, keeps the near-miss pings the rules accept (99 m, 149 km/h), and uses the original copy of each duplicate. It then sums straight-line chords, clipped to the zone, split at midnight, and breaks trips at G. This is separate code (it never filters, it just knows), with the same geometry model. ±1 m is reachable. Any miss shows a real bug in detection or ordering.
- **(b) Along-road arc length** of the continuous simulated path. This is the "physical" truth, but straight-line chords on curves will differ by far more than 1 m per day. Under (b) the ±1 m criterion would fail by design.

**Proposal:** use (a) as the pass/fail truth. Also report (b) as an extra column, `road_km`, so the item 8 limitation is **quantified** and not hidden.

**Consequence:** good pings will carry **no** random GPS jitter. If they did, the reported positions would differ from the clean ones, and no filter can remove that. Truth (a) would then be computed on different points than the fixed code sees, and ±1 m would fail. Jitter would also make the near-threshold pings (99 vs 101 m) harder to place exactly. I propose no jitter on good pings and **controlled offsets** only on injected faults and near-miss pings. Near-miss pings are accepted by the rules, so truth uses their *reported* position. Alternative: add jitter and define truth on the reported positions of non-fault pings. That's more realistic, but truth then depends on the same reported data the code sees.

## 12. Metric and projection (extra)

**Proposal (ASSUMPTION):** a fictional area near Seoul (UTC+9, about 37.5°N 127.0°E), all synthetic. All lengths, road distances and zone clipping use one **local azimuthal-equidistant projection** (pyproj, centred on the zone centroid), in both the fixed code and the truth.

**Implementation detail (fixed at step 3):** the centre is the centroid of the `zone.geojson` polygon computed in lon/lat degrees (shapely `centroid`), WGS84. For the current data that is lon 127.0003097, lat 37.5118317. Both `generate_data.py` and `distance_fixed.py` derive it from the file independently.

**Why not UTM or haversine:**
- UTM's scale factor (0.9996 near the central meridian) is off by up to about 0.4 m per km. Over 50+ km per day that alone breaks ±1 m if truth uses a different metric.
- The buggy version uses haversine, on purpose. The QA report will separately show how big the metric difference is (expected: centimetres per day within about 15 km of the centre).

## 13. Threshold inclusivity (extra)

**Proposal (ASSUMPTION):**
- R1 rejects at `off_road_m > 100.0` (exactly 100.0 is kept).
- R2 rejects at `implied_kmh > 150.0`.
- A local day is the half-open interval `[00:00, 24:00)` UTC+9. A ping at exactly `15:00:00Z` belongs to the **new** local day.
- A point exactly on the zone boundary counts as `in_zone` (shapely `covers`). This has no effect on clipped lengths.

## 14. Pings outside the zone (extra)

**Proposal (ASSUMPTION):** don't drop out-of-zone pings before building segments. They are the endpoints of the segments that enter and leave the zone. Filtering pings by zone first would lose every entry and exit portion. That would underpay the driver, and it's a plausible second bug.

## 15. Midnight and zone edge in one segment (extra)

**Proposal (ASSUMPTION):** split at midnight first (item 6), then clip each piece to the zone (item 7). The order is equivalent for straight lines, but this order keeps the code simple.

## 16. Batch vs streaming (extra)

**Proposal (ASSUMPTION):** each run recomputes all days from the complete file. A late ping that arrives after its day was "reported" still lands in its own day (R4). Re-issuing a closed invoice is out of scope; I'll list it under "what's still not solved".

---

## Still not solved (carried to `directive.md`)

- **Bad first anchor (from item 3, Zain):** a bad ping that is within 100 m of the road and becomes the first accepted ping (e.g. first of a trip, so R2 can't check it) can make later good pings look too fast and get them rejected.
- **Late pings after a day's report was issued (Zain):** R4 places them in their own (past) day, but restating an already issued report or invoice is not handled.
- **First ping of a trip** has no R2 reference (item 2).
- **Straight-line geometry** under-counts on curves and across gaps (item 8); measured by the `road_km` column.
- **Cost of the > 5-minute trip-break rule (assumption 9), measured:** driving during an in-trip gap of more than 5 minutes is not paid. Measured on the synthetic data with `python data/generate_data.py` (column `trip_break_road_km` in `data/truth_distance.csv`: along-road in-zone km driven between the last ping before such a gap and the first ping after it):

  | Driver | Local day | Unpaid along-road in-zone km in > 5-min gaps | truth − along-road km (all causes) |
  |--------|-----------|---------------------------------------------:|-----------------------------------:|
  | D02 | 2026-10-12 | 0.689 | +0.473 |
  | D05 | 2026-10-13 | **3.663** | **−4.952** |
  | D06 | 2026-10-14 | 1.573 | −1.619 |
  | all other 15 driver-days | | 0.000 | |
  | **Total** | | **5.925** (1.71% of the 347.456 along-road km) | −4.996 |

  For D05 2026-10-13, 3.663 km of the −4.952 km difference from the along-road distance is driving inside a > 5-min gap. The rest is the straight-line effect. The 5-minute threshold is a **business decision**, not something the rules state: a longer threshold pays more of this distance but bridges more real stops with a straight line; a shorter one pays less. The data only shows the cost at 5 minutes.
- **Gap distance is a straight-line estimate (item 9, Zain):** it can include or miss in-zone distance where the road bends across the zone edge during the gap. Next step: estimate gaps along the road path.
