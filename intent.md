# Intent: GPS ping distance (MUST Company Quest)

> **SYNTHETIC:** every number here comes from the synthetic dataset generated for the MUST Company hiring Quest, not real data.
> **Submitted by Muhammad Zain Shahzad.** I set the brief, made the interpretation and ranking decisions, and reviewed the results. Claude Code assisted with implementation; [AI_LOG.md](AI_LOG.md) records what it produced and what I checked, changed, or rejected. Every number comes from a command in [`docs/findings.md`](docs/findings.md) ("How the numbers were produced").
>
> **What existed before:** nothing. All code, data and documents were created new for this Quest on 4 Nov 2026; no earlier work was reused.

## The problem

Advertisers pay per km driven **inside a campaign zone**, computed from 10-second GPS pings. The reference calculation bills **925.741 km** where the rules give **342.459 km**. That's **+583.282 km (+170.3%) overbilled**. At the same time, some drivers are **underpaid** on specific days, by up to **−44.4%** (D04, 2026-10-14).

## Problems found, and how

They were found by a QA tool (`qa_check.py`) that never reads the ground truth of injected faults.
- It flags every ping against R1–R6.
- It lists every segment the buggy version counts, with what's wrong with it.
- It measures each problem by re-running the buggy sum with problems corrected one at a time.

A separate step (`verify_tool.py`) scores the tool against the fault manifest. Precision and recall were 1.000 for every fault type, and none of the 32 near-miss pings (just under 100 m or 150 km/h) was rejected.

That score shows the tool applies the rules consistently at the thresholds. It doesn't show the rules would catch noise of a kind the generator never produced.

| # | Problem | How it showed up | Advertiser impact (overbilling, cumulative) | Driver impact |
|---|---------|------------------|---------------------------------------------|---------------|
| P1 | Late and out-of-order pings summed in **file order** (R4) | 158 rows out of order; 88 counted segments run backwards in time | **+404.747 km (+118.2%)** | km lands on the wrong days |
| P5 | Days cut at **UTC** midnight, not 00:00 UTC+9 (R5) | buggy invoices for 2026-10-11, a date with no local driving | net 0 km, but **77.279 km** on the wrong driver-day | **the only underpayment**: −25.9% D01 10-13, −44.4% D04 10-14 |
| P3 | GPS **noise** not filtered (R1 off-road, R2 speed) | 102 pings rejected (56 off-road, 46 speed) | **+58.931 km (+17.2%)** | paid for jumps never driven |
| P4 | Whole segment counted if **either end** is in the zone (R6) | 430 counted segments with one end outside | **+56.538 km (+16.5%)** | none measured (0 segments cut a zone corner) |
| P2 | Distance counted **across trip breaks** (arises from assumption 9, not a stated rule) | 51 counted segments span > 5 min | **+62.785 km (+18.3%)** | paid for distance between trips |
| P6 | **Duplicates** counted (R3) | 180 copies (120 exact, 60 shifted 2.3–11.3 m) | +0.181 km (+0.1%) | negligible |
| P7 | Spherical **haversine** vs the local projection | metric residual ≤ 9.0 m per driver-day | about +0.1 km | none, but 9× the ±1 m tolerance |

Evidence for each problem (rows, a driver-day example, a figure and the line in `src/reference_buggy.py`) is in [`docs/findings.md`](docs/findings.md).

## My ranking, and why

**P1, P5, P3, P4, P2, P6, P7.** My reasons:
- **P1:** the largest overbilling (+118%), and it blocks every other rule.
- **P5:** second because it is the only problem that underpays drivers and creates invoices for days nobody drove, even though the net km is zero.
- **P3 and P4:** they break explicit rules (R1/R2 and R6).
- **P2:** it comes from my item 9 assumption rather than a written rule, so it ranks below the explicit-rule problems.
- **P6:** an explicit rule but tiny in km.
- **P7:** a precision issue, not a rule violation, but the fix needs it to meet ±1 m.

The AI's proposal had P2 above P4. I swapped them (`AI_LOG.md` row 20).

## The problem I chose to solve first: P1

P1 is the biggest error, and the other rules can't work without it:
- R2 speeds, trip gaps and the midnight split are meaningless on unsorted data.

**Root cause:** `src/reference_buggy.py:41–47` walks each driver's rows in received order and sums each row to the *previous row*, not the previous timestamp.

**Example:** row 7669 (D06) arrived **30.3 h** late.
- **Buggy version:** joined to a ping from two days later, it becomes an **11.691 km** segment, counted inside the zone and filed under the wrong date.
- **Placed by its own timestamp:** it adds 131.6 m twice and **0 m in the zone**.

**I required the fix to correct P1–P7 anyway**, because the regression suite has to cover noise, duplicates, late pings, the UTC+9 cut and the zone boundary.

**Result:**
- Fixed = truth **within 1 m on 18 of 18 driver-days**; the largest difference is about 4×10⁻⁷ m.
- 12 of 12 old-behaviour tests fail on the buggy code; 18 of 18 pass on the fixed code.
- All 8 mutants of the fixed code are caught.

## What was left out (and why)

- **Snapping segments to the road** (map matching). Segments are straight lines (assumption 8). The cost is measured, not hidden:
  - truth − along-road distance ranges from −4.952 to +0.977 km per driver-day.
  - The > 5-minute trip-break rule leaves **5.925 km** of real in-zone driving unpaid. 3.663 km of that is on D05, 2026-10-13.
  - The right threshold is a business decision.
- **Gap estimates along the road:** a straight line across a gap can cut through, or miss, the zone where the road bends across the edge. The next step would be estimating gaps along the road path.
- **Restating reports already issued** when a very late ping arrives (8 pings arrive 30–40 h late). The pings are placed in their own past day, but no restatement or credit note is produced.
- **A bad first anchor:** a bad ping within 100 m that becomes the first accepted ping of a trip can make later good pings look too fast. R2 can't check the first ping of a trip.
- **Real data, a database and streaming.** This is a batch tool over one synthetic file. Performance at production scale was not measured.
