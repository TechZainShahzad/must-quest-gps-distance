"""Regression suite: one small, hand-checkable fixture per rule. SYNTHETIC: MUST Company hiring Quest.

Every test runs on both implementations. The expected outcome is declared per test:
- `both(buggy_fails=True)`:  fails on reference_buggy (xfail, strict) and passes on distance_fixed.
- `both(buggy_fails=False)`: a control case where the old behaviour happens to be right; it
  must pass on both (so the suite also catches a fix that breaks what used to work).
Expected metres are written as arithmetic on the fixture coordinates (see conftest.py).
"""
from __future__ import annotations

import ast
from math import hypot
from pathlib import Path

import pandas as pd
import pytest

from conftest import BASE, ROAD, ZONE, assert_matches, both, pings, run

D = "T01"
DAY = "2026-10-12"
ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------- R1 off-road

@both(buggy_fails=False)
def test_r1_offroad_99m_is_kept(impl):
    df = pings((0, 0, 0), (10, 100, 99), (20, 200, 0))
    assert_matches(run(impl, df), {(D, DAY): 2 * hypot(100, 99)})


@both(buggy_fails=True, reason="no off-road filter")
def test_r1_offroad_101m_is_ignored(impl):
    df = pings((0, 0, 0), (10, 100, 101), (20, 200, 0))
    assert_matches(run(impl, df), {(D, DAY): 200.0})


# --------------------------------------------------------------------- R2 speed

@both(buggy_fails=False)
def test_r2_speed_149kmh_is_kept(impl):
    d = 149 / 3.6 * 10                      # 413.889 m in 10 s
    df = pings((0, 0, 0), (10, d, 0), (20, 300, 0))
    assert_matches(run(impl, df), {(D, DAY): d + (d - 300)})


@both(buggy_fails=True, reason="no speed filter")
def test_r2_speed_151kmh_is_ignored(impl):
    d = 151 / 3.6 * 10                      # 419.444 m in 10 s
    df = pings((0, 0, 0), (10, d, 0), (20, 300, 0))
    assert_matches(run(impl, df), {(D, DAY): 300.0})


@both(buggy_fails=True, reason="no speed filter")
def test_r2_noise_ping_then_good_ping_uses_last_accepted(impl):
    # spike at t=20 (600 m in 10 s = 216 km/h). The good ping at t=30 is 450 m from the spike
    # (162 km/h vs the raw previous ping) but only 161.6 m from the last ACCEPTED ping (29 km/h): kept.
    df = pings((0, 0, 0), (10, 150, 0), (20, 750, 0), (30, 300, 60), (40, 450, 0))
    assert_matches(run(impl, df), {(D, DAY): 150 + 2 * hypot(150, 60)})


# --------------------------------------------------------------------- R3 duplicates

@both(buggy_fails=False)
def test_r3_exact_duplicate_adjacent_counts_once(impl):
    df = pings((0, 0, 0), (10, 100, 0), (10, 100, 0), (20, 200, 0))
    assert_matches(run(impl, df), {(D, DAY): 200.0})


@both(buggy_fails=True, reason="duplicates are counted")
def test_r3_exact_duplicate_resent_later_counts_once(impl):
    df = pings((0, 0, 0), (10, 100, 0), (20, 200, 0), (10, 100, 0), (30, 300, 0))
    assert_matches(run(impl, df), {(D, DAY): 300.0})


@both(buggy_fails=True, reason="duplicates are counted")
def test_r3_same_timestamp_different_coords_keeps_closest_to_road(impl):
    # shifted copy (30 m off the road) is received FIRST; the rule keeps the on-road copy.
    # keep-first would give 2*hypot(100, 30) = 208.8 m; the buggy sum gives 104.4 + 30 + 100.
    df = pings((0, 0, 0), (10, 100, 30), (10, 100, 0), (20, 200, 0))
    assert_matches(run(impl, df), {(D, DAY): 200.0})


# --------------------------------------------------------------------- R4 late / out of order

@both(buggy_fails=True, reason="sums in file order")
def test_r4_late_ping_is_placed_by_its_timestamp(impl):
    df = pings((0, 0, 0), (10, 100, 0), (30, 300, 0), (20, 200, 40))   # t=20 arrives after t=30
    assert_matches(run(impl, df), {(D, DAY): 100 + 2 * hypot(100, 40)})


# --------------------------------------------------------------------- R5 local day (UTC+9)

@both(buggy_fails=True, reason="groups by UTC date")
def test_r5_trip_across_local_midnight_is_split_by_time(impl):
    df = pings(("2026-10-12T14:59:50Z", 0, 0), ("2026-10-12T15:00:10Z", 200, 0))
    assert_matches(run(impl, df), {(D, "2026-10-12"): 100.0, (D, "2026-10-13"): 100.0})


@both(buggy_fails=True, reason="groups by UTC date")
def test_r5_ping_at_exactly_1500z_starts_the_new_local_day(impl):
    df = pings(("2026-10-12T14:59:50Z", 0, 0), ("2026-10-12T15:00:00Z", 100, 0), ("2026-10-12T15:00:10Z", 200, 0))
    assert_matches(run(impl, df), {(D, "2026-10-12"): 100.0, (D, "2026-10-13"): 100.0})


@both(buggy_fails=True, reason="groups by UTC date")
def test_r5_morning_trip_belongs_to_the_local_day(impl):
    # 07:30 local on 2026-10-12 is 22:30 UTC on 2026-10-11
    df = pings(("2026-10-11T22:30:00Z", 0, 0), ("2026-10-11T22:30:10Z", 150, 0))
    assert_matches(run(impl, df), {(D, "2026-10-12"): 150.0})


# --------------------------------------------------------------------- R6 zone

@both(buggy_fails=True, reason="counts the whole segment if either end is inside")
def test_r6_segment_crossing_zone_edge_counts_inside_part_only(impl):
    df = pings((0, 900, 0), (10, 1100, 0))                   # edge at x = 1000
    assert_matches(run(impl, df), {(D, DAY): 100.0})


@both(buggy_fails=False)
def test_r6_segment_entirely_outside_counts_zero(impl):
    df = pings((0, 1100, 0), (10, 1300, 0))
    assert_matches(run(impl, df), {})


@both(buggy_fails=False)
def test_r6_segment_entirely_inside_counts_fully(impl):
    df = pings((0, 0, 0), (10, 200, 0))
    assert_matches(run(impl, df), {(D, DAY): 200.0})


# --------------------------------------------------------------------- assumption 9 gaps

@both(buggy_fails=True, reason="no trip concept")
def test_gap_over_5_minutes_ends_the_trip(impl):
    df = pings((0, 0, 0), (10, 100, 0), (410, 300, 0))       # 400 s gap
    assert_matches(run(impl, df), {(D, DAY): 100.0})


@both(buggy_fails=False)
def test_gap_of_2_minutes_is_counted_as_straight_line(impl):
    df = pings((0, 0, 0), (10, 100, 0), (130, 400, 0))       # 120 s gap, 9 km/h: counted (estimated)
    assert_matches(run(impl, df), {(D, DAY): 400.0})


# --------------------------------------------------------------------- full dataset

@pytest.fixture(scope="module")
def dataset():
    if not (ROOT / "data/pings.csv").exists():
        pytest.skip("run data/generate_data.py first")
    from src.geo import load_geom
    return (pd.read_csv(ROOT / "data/pings.csv"), load_geom(ROOT / "data/road.geojson"),
            load_geom(ROOT / "data/zone.geojson"), pd.read_csv(ROOT / "data/truth_distance.csv"))


@both(buggy_fails=True, reason="every fault above, at scale")
def test_full_dataset_matches_truth_within_1m(impl, dataset):
    p, road, zone, truth = dataset
    out = impl.compute(p, road, zone)
    day = "local_day" if "local_day" in out else "day"
    got = {(d, k): float(m) for d, k, m in zip(out.driver_id, out[day], out["m"] if "m" in out else out.km * 1000)}
    assert_matches(got, {(d, k): float(m) for d, k, m in zip(truth.driver_id, truth.local_day, truth.truth_m)})


# --------------------------------------------------------------------- independence of truth

def test_generator_does_not_import_src():
    """Zain's CHECKPOINT 1 condition: truth is separate code that does not import distance_fixed."""
    tree = ast.parse((ROOT / "data/generate_data.py").read_text(encoding="utf-8"))
    mods = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    mods += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not [m for m in mods if m == "src" or m.startswith("src.") or "distance_fixed" in m or "ping_rules" in m]


def test_fixture_frame_sanity():
    """The fixture road and zone are what the docstring says (guards the hand-checked numbers)."""
    assert abs(ROAD.length) > 0 and ZONE.is_valid
    assert BASE.isoformat().startswith("2026-10-12T01:00:00")
