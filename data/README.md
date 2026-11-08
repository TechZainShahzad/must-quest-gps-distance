# data/: SYNTHETIC dataset

> **SYNTHETIC: generated for MUST Company hiring Quest, not real data.** The road, the zone, the drivers and every ping are invented. The coordinates are placed near Seoul (UTC+9) only so that the timezone rule makes sense.

Regenerate it with `python data/generate_data.py` (fixed seed `20261104`; the output is byte-identical across runs, checked with md5).

## Files

| File | Content |
|------|---------|
| `road.geojson` | road path, a LineString, lon/lat WGS84, 25.885 km |
| `zone.geojson` | campaign zone, a Polygon. The road crosses its edge 8 times |
| `pings.csv` | `driver_id, ts_utc, lat, lon` in **received order** (not sorted). 7,675 rows, 6 drivers, 41 trips, local days 2026-10-12 to 2026-10-14 |
| `fault_manifest.csv` | `row_id, fault_type, detail, value`: ground truth for what was injected. **The QA tool and both implementations must not read it.** Only `verify_tool.py` reads it |
| `truth_distance.csv` | true in-zone distance per driver per local day (see below) |
| `sanity_plot.png` | road, zone and pings coloured by injected fault |

`row_id` is the 0-based index of the data row in `pings.csv`, with the header excluded. One row can have several manifest entries.

## Fault types in `fault_manifest.csv`

| fault_type | What | Rule | Expected outcome |
|------------|------|------|------------------|
| `noise_offroad` | `big`: 300–2,500 m jump; `just_over`: 100.15–103 m from the road | R1 | rejected |
| `nearmiss_offroad` | 96.5–99.85 m from the road | R1 | **accepted** |
| `noise_speed` | on the road, but implies `big` > 160 km/h or `just_over` 150.2–153 km/h vs the previous accepted ping | R2 | rejected |
| `nearmiss_speed` | on the road, implies 146–149.8 km/h | R2 | **accepted** |
| `dup_exact` | the extra copy of an identical row | R3 | counted once |
| `dup_coord` | same driver and timestamp, shifted 2–12.8 m (designed range; the measured spread is in `value`). The original is always the copy closer to the road. `variant_first` = the shifted copy is received first | R3 (item 4: keep the copy closest to the road) | dropped |
| `late` | `late`: received 15 s–40 min after its timestamp; `very_late`: 30–40 h later (after its day's report). Each one is overtaken by a later ping of the same driver | R4 | placed by its own timestamp |
| `midnight_cross` | first ping on or after 00:00 UTC+9 in a trip that crosses midnight; `exact_0000` = the ping is at exactly 15:00:00Z | R5 | split at midnight |
| `zone_edge_inside` / `zone_edge_outside` | clean ping within 3 m of the zone edge (`forced` = the trip start was shifted to put it there) | R6 | — |
| `gap_short` / `gap_estimated` / `gap_trip_break` | row of the first ping after 2–5 / 7–28 / 32–60 missing pings; `value` = gap in s | item 9 | counted / counted + flagged estimated / not counted |

The `value` column holds the measured value: off-road m, km/h, spread m, delay s or gap s. Measurements are taken after the coordinates are rounded to 7 decimals, in the truth projection.

Not injected (documented limitation, assumptions item 2): a noise ping as the first ping of a trip.

## Truth (`truth_distance.csv`)

Separate code in `generate_data.py` computes it. It **does not import anything from `src/`** (Zain's condition, CHECKPOINT 1).

- `truth_m` / `truth_km`: the confirmed rules applied with perfect knowledge of the injected faults. It drops injected noise and duplicate copies, keeps near-misses at their reported position, and orders by timestamp. Segments are straight lines in an AEQD projection centred on the zone centroid, split at local midnight by time and clipped to the zone. There is no distance across a received-timestamp gap > 300 s. `truth_m` is the unrounded value to compare against (±1 m).
- `road_km`: the **physical** along-road in-zone distance from the 1 s simulation, including the time inside gaps. It is information only, not pass/fail.
- `truth_minus_road_km`: `truth_km − road_km` (computed from unrounded metres).
- `estimated_km`: the part of `truth_km` that comes from segments flagged `estimated` (Δt > 60 s).
- `trip_break_road_km`: along-road in-zone km driven during in-trip gaps > 5 min (from the last ping before the gap to the first ping after it). Not paid under assumption 9.
- `n_segments`, `n_estimated` (Δt > 60 s), `n_trip_breaks` (> 300 s, including the breaks between trips).

**Built-in self-checks** (the generator stops if any fails):
- every truth ping passes R1 and R2 against the previous truth ping
- every injected noise ping fails its rule against the previous truth ping
- every `dup_coord` shifted copy is > 0.5 m farther from the road than its original
- the intentionally delayed rows are exactly the rows that arrive out of order

## Sanity check (output of `python data/generate_data.py`)

```
SYNTHETIC dataset summary
seed=20261104  rows=7675  drivers=6  trips=41
road length=25.885 km  road/zone-edge crossings=8
projection centre (zone centroid, lon/lat): 127.0003097, 37.5118317

fault_manifest counts:
fault_type         detail        
dup_coord          variant_first      26
                   variant_second     34
dup_exact                            120
gap_estimated      (all lengths)      17
gap_short          (all lengths)      14
gap_trip_break     (all lengths)       4
late               late              150
                   very_late           8
midnight_cross     exact_0000          2
                   inexact             3
nearmiss_offroad   just_under         16
nearmiss_speed     just_under         16
noise_offroad      big                40
                   just_over          16
noise_speed        big                30
                   just_over          16
zone_edge_inside   forced              8
                   natural             5
zone_edge_outside  forced             14
                   natural             2

truth self-checks: 7348 R2 checks, 7348 segments, 17 estimated (>60 s), 39 breaks >300 s (between trips + injected gap_trip_break)

truth_distance.csv:
driver_id  local_day  truth_km      truth_m  road_km  truth_minus_road_km  estimated_km  trip_break_road_km  n_segments  n_estimated  n_trip_breaks
      D01 2026-10-12    21.846 21846.164192   20.938                0.908         1.993               0.000         440            2              2
      D01 2026-10-13    28.888 28888.053215   29.185               -0.297         1.104               0.000         573            2              2
      D01 2026-10-14    12.472 12471.500569   12.502               -0.030         0.000               0.000         327            0              2
      D02 2026-10-12    23.261 23261.226724   22.788                0.473         1.834               0.689         462            1              3
      D02 2026-10-13    20.152 20152.072250   20.449               -0.297         0.257               0.000         500            1              2
      D02 2026-10-14    14.856 14855.961026   14.562                0.294         0.000               0.000         377            0              2
      D03 2026-10-12    23.165 23165.006705   23.238               -0.073         0.000               0.000         416            1              2
      D03 2026-10-13    25.449 25448.844690   25.348                0.101         0.333               0.000         427            2              2
      D03 2026-10-14    11.067 11066.809997   11.100               -0.033         1.101               0.000         251            1              2
      D04 2026-10-12    22.284 22283.555624   21.307                0.977         0.868               0.000         409            1              1
      D04 2026-10-13    16.968 16967.910510   16.928                0.040         0.000               0.000         477            0              3
      D04 2026-10-14    21.205 21205.213015   21.794               -0.589         1.074               0.000         469            1              3
      D05 2026-10-12    18.312 18311.896975   18.372               -0.060         0.000               0.000         411            1              1
      D05 2026-10-13    19.691 19690.634819   24.643               -4.952         0.893               3.663         337            1              4
      D05 2026-10-14    18.957 18957.433888   19.017               -0.060         0.000               0.000         469            2              2
      D06 2026-10-12    14.519 14519.325433   14.263                0.257         1.268               0.000         318            1              1
      D06 2026-10-13    16.018 16018.120476   16.054               -0.036         0.000               0.000         384            0              2
      D06 2026-10-14    13.349 13349.156246   14.968               -1.619         0.000               1.573         304            0              3

TOTAL truth_km=342.459  road_km=347.456
```

## Observation: gaps vs the zone edge (for Zain; affects assumptions item 9)

`truth_km` is higher than `road_km` on some driver-days. Straight lines are always shorter than the road in total. The excess comes from two sources:

1. **Near-miss pings:** an accepted 99 m sideways or 149 km/h forward position adds a zig-zag.
2. **Gaps bridged by one straight line (item 9 decision):** where the road dips out of the zone during a 70–280 s gap, the straight line cuts across the zone and counts distance that was driven outside it. Where the road bulges into the zone, the line misses it.

The size of this effect on the current data will be measured by the QA tool in step 5 (per segment, `estimated` flag vs `road_km`) and reported in `docs/findings.md`, with the command shown. This is how the rules behave, not a bug; the `estimated` flag exists to surface it.
