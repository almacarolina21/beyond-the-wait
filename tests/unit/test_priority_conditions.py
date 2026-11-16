"""Priority behavior under different resource conditions (deterministic, hand-checked).

Milestone 4 mutation testing showed only one test guarded ESI priority; these
add coverage for scarce spaces, multi-slot physicians, shift changes, and an
intervention that relocates waiting instead of removing it.

Note: we deliberately do NOT assert that adding capacity can never increase
any individual patient's wait. With interacting stochastic queues that is not
a valid invariant (see the triage example below).
"""

from __future__ import annotations

from typing import Any

from beyond_the_wait.simulation.engine import run_simulation
from tests.sim_helpers import durations, patients, row, settings, tiny_site

ONE_BED = {"acute_beds": 1, "resuscitation_bays": 1, "fast_track_chairs": 0, "hallway_spaces": 0}


def test_scarce_spaces_go_to_higher_acuity_first(site_dict: dict[str, Any]) -> None:
    # One acute bed held by P1 (5-35 exam, 10 discharge -> leaves 45).
    # P2 (ESI 4) is ready at 10; P3 (ESI 2) at 25. At 45 the bed goes to P3.
    site = tiny_site(
        site_dict,
        spaces=ONE_BED,
        physicians={"day": 3, "evening": 3, "night": 3},
        triage_nurses={"day": 3, "evening": 3, "night": 3},
    )
    pts = patients(
        (0, 3, False, False, False), (5, 4, False, False, False), (20, 2, False, False, False)
    )
    r = run_simulation(site, settings(), patients=pts, durations=durations(3), bed_release_times=[])
    assert row(r, 1).departure_min == 45
    assert row(r, 3).space_start_min == 45
    assert row(r, 2).space_start_min == row(r, 3).departure_min


def test_priority_with_two_physician_slots(site_dict: dict[str, Any]) -> None:
    """One physician, concurrency 2 (two exam slots), triage never a bottleneck.
      P1 ESI 3 arrives 0: triage 0-5, exam 5-35     (slot A)
      P2 ESI 3 arrives 3: triage 3-8, exam 8-38     (slot B)
      P3 ESI 3 arrives 4: ready 9, waits
      P4 ESI 1 arrives 10: bypasses triage, ready 10, waits
    At 35 slot A frees: ESI 1 goes first even though P3 was ready earlier.
    At 38 slot B frees: P3."""
    site = tiny_site(site_dict, concurrency=2, triage_nurses={"day": 4, "evening": 4, "night": 4})
    pts = patients(
        (0, 3, False, False, False),
        (3, 3, False, False, False),
        (4, 3, False, False, False),
        (10, 1, False, False, False),
    )
    r = run_simulation(site, settings(), patients=pts, durations=durations(4), bed_release_times=[])
    assert (row(r, 1).provider_start_min, row(r, 2).provider_start_min) == (5, 8)
    assert row(r, 4).provider_start_min == 35
    assert row(r, 3).provider_start_min == 38


def test_priority_holds_when_capacity_rises_at_shift_change(site_dict: dict[str, Any]) -> None:
    # Day: 1 physician (busy 14:00-16:00 with P1). Evening adds a second at 15:00.
    # Waiting: ESI 5 (ready 14:10) and ESI 2 (ready 14:30). The new slot goes to ESI 2.
    site = tiny_site(site_dict, physicians={"day": 1, "evening": 2, "night": 1})
    pts = patients(
        (835, 3, False, False, False), (845, 5, False, False, False), (865, 2, False, False, False)
    )
    r = run_simulation(
        site, settings(), patients=pts, durations=durations(3, provider=120), bed_release_times=[]
    )
    assert row(r, 3).provider_start_min == 900
    assert row(r, 2).provider_start_min == row(r, 1).provider_end_min == 840 + 120


def test_extra_triage_nurse_moves_waiting_downstream(site_dict: dict[str, Any]) -> None:
    """Two simultaneous arrivals, one physician (30-min exams).
    1 triage nurse: P2 waits 5 for triage, then 25 for the physician.
    2 triage nurses: P2 waits 0 for triage, then 30 for the physician.
    Triage congestion is removed, provider congestion grows, arrival-to-provider unchanged."""
    pts = patients((0, 3, False, False, False), (0, 3, False, False, False))
    one = run_simulation(
        tiny_site(site_dict), settings(), patients=pts, durations=durations(2), bed_release_times=[]
    )
    two = run_simulation(
        tiny_site(site_dict, triage_nurses={"day": 2, "evening": 2, "night": 2}),
        settings(),
        patients=pts,
        durations=durations(2),
        bed_release_times=[],
    )
    p1, p2 = row(one, 2), row(two, 2)
    assert (p1.triage_wait_min, p1.provider_wait_min) == (5, 25)
    assert (p2.triage_wait_min, p2.provider_wait_min) == (0, 30)
    assert p1.arrival_to_provider_min == p2.arrival_to_provider_min == 35


def test_boarding_queue_ignores_acuity(site_dict: dict[str, Any]) -> None:
    # Bed queue is FIFO: an ESI-1 admitted after an ESI-4 waits behind it.
    site = tiny_site(
        site_dict,
        physicians={"day": 3, "evening": 3, "night": 3},
        triage_nurses={"day": 3, "evening": 3, "night": 3},
    )
    pts = patients((0, 4, False, False, True), (10, 1, False, False, True))
    r = run_simulation(
        site, settings(), patients=pts, durations=durations(2), bed_release_times=[300.0, 400.0]
    )
    assert row(r, 1).bed_assigned_min == 300 and row(r, 2).bed_assigned_min == 400
