"""Small, fully deterministic simulations with hand-checked answers.

Every patient, task duration, and bed release is fixed, so each expected
time below can be worked out with pencil and paper.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from beyond_the_wait.simulation.engine import SimulationError, run_simulation
from beyond_the_wait.simulation.validity import run_checks
from tests.sim_helpers import durations, patients, row, settings, tiny_site


@pytest.fixture
def tiny(site_dict: dict[str, Any]):  # noqa: ANN201
    return tiny_site(site_dict)


# --- Discharge and admission paths ---------------------------------------------------


def test_discharge_path_timeline(tiny) -> None:  # noqa: ANN001
    # 01:00 arrival, ESI 3, lab (60) and imaging (90) in parallel -> wait 90.
    r = run_simulation(
        tiny,
        settings(),
        patients=patients((60, 3, True, True, False)),
        durations=durations(1),
        bed_release_times=[],
    )
    p = row(r, 1)
    assert (p.triage_start_min, p.triage_end_min) == (60, 65)
    assert p.space_start_min == 65 and p.provider_start_min == 65 and p.provider_end_min == 95
    assert p.disposition_min == 95 + 90  # slower of lab/imaging
    assert p.departure_min == 185 + 10  # discharge process
    assert p.los_min == 135 and p.arrival_to_provider_min == 5
    assert p.status == "completed" and p.disposition == "discharge"
    assert pd.isna(p.boarding_min)


def test_no_diagnostics_goes_straight_to_disposition(tiny) -> None:  # noqa: ANN001
    r = run_simulation(
        tiny,
        settings(),
        patients=patients((0, 4, False, False, False)),
        durations=durations(1),
        bed_release_times=[],
    )
    p = row(r, 1)
    assert p.disposition_min == p.provider_end_min == 35


def test_admission_path_boards_until_bed_release(tiny) -> None:  # noqa: ANN001
    r = run_simulation(
        tiny,
        settings(),
        patients=patients((0, 2, False, False, True)),
        durations=durations(1, provider=20),
        bed_release_times=[100.0],
    )
    p = row(r, 1)
    assert p.boarding_start_min == 25 and p.bed_assigned_min == 100
    assert p.boarding_min == 75 and p.departure_min == 100
    assert p.los_min == 100 and p.stage_at_stop == "departed"
    # The ED space stays occupied while boarding, then is freed at departure.
    occupied = r.spaces.occupied_series["acute_beds"]
    assert occupied.integral(0, 200) == pytest.approx(100 - 5)


def test_initially_available_bed_means_no_boarding(site_dict: dict[str, Any]) -> None:
    site = tiny_site(site_dict, initial_beds=1, max_unclaimed=1)
    r = run_simulation(
        site,
        settings(),
        patients=patients((0, 2, False, False, True)),
        durations=durations(1),
        bed_release_times=[],
    )
    assert row(r, 1).boarding_min == 0


def test_boarders_claim_beds_first_come_first_served(tiny) -> None:  # noqa: ANN001
    pts = patients((0, 3, False, False, True), (1, 2, False, False, True))
    r = run_simulation(
        tiny, settings(), patients=pts, durations=durations(2), bed_release_times=[200.0, 300.0]
    )
    assert row(r, 1).bed_assigned_min == 200  # admitted first (ESI 3)
    assert row(r, 2).bed_assigned_min == 300  # ESI 2 does not jump the bed queue


# --- Priority behavior ---------------------------------------------------------------


def test_physician_queue_serves_higher_acuity_first(tiny) -> None:  # noqa: ANN001
    # One physician. P1 (ESI 4) occupies them 5-65. P2 (ESI 4) and P3 (ESI 2) wait.
    pts = patients(
        (0, 4, False, False, False), (10, 4, False, False, False), (20, 2, False, False, False)
    )
    r = run_simulation(
        tiny, settings(), patients=pts, durations=durations(3, provider=60), bed_release_times=[]
    )
    assert row(r, 1).provider_end_min == 65  # never interrupted
    assert row(r, 3).provider_start_min == 65  # ESI 2 goes first...
    assert row(r, 2).provider_start_min == 125  # ...even though ESI 4 was ready earlier


def test_same_acuity_is_first_come_first_served(tiny) -> None:  # noqa: ANN001
    pts = patients(
        (0, 3, False, False, False), (10, 3, False, False, False), (20, 3, False, False, False)
    )
    r = run_simulation(
        tiny, settings(), patients=pts, durations=durations(3, provider=60), bed_release_times=[]
    )
    starts = [row(r, i).provider_start_min for i in (1, 2, 3)]
    assert starts == [5, 65, 125]


def test_triage_is_first_come_first_served_regardless_of_acuity(tiny) -> None:  # noqa: ANN001
    pts = patients((0, 5, False, False, False), (1, 2, False, False, False))
    r = run_simulation(
        tiny, settings(), patients=pts, durations=durations(2, triage=10), bed_release_times=[]
    )
    assert row(r, 2).triage_start_min == 10  # ESI 2 waits: acuity unknown before triage


def test_esi1_bypasses_triage_but_does_not_preempt(tiny) -> None:  # noqa: ANN001
    # P1 is being examined 5-65 by the only physician when ESI-1 P2 arrives at 10.
    pts = patients((0, 3, False, False, False), (10, 1, False, False, False))
    r = run_simulation(
        tiny, settings(), patients=pts, durations=durations(2, provider=60), bed_release_times=[]
    )
    p2 = row(r, 2)
    assert bool(p2.bypassed_triage) and p2.triage_wait_min == 0
    assert p2.space_start_min == 10 and p2.space_type == "resuscitation_bays"
    assert p2.provider_start_min == 65  # waits for the current exam to end
    assert row(r, 1).provider_end_min == 65


def test_without_bypass_esi1_waits_in_triage(site_dict: dict[str, Any]) -> None:
    site = tiny_site(site_dict, bypass=False)
    pts = patients((0, 3, False, False, False), (1, 1, False, False, False))
    r = run_simulation(
        site, settings(), patients=pts, durations=durations(2, triage=10), bed_release_times=[]
    )
    assert row(r, 2).triage_start_min == 10 and not row(r, 2).bypassed_triage


def test_space_preference_and_no_head_of_line_blocking(site_dict: dict[str, Any]) -> None:
    # One acute bed and one chair. ESI 2 (acute only) waits for the acute bed;
    # a later ESI 4 (chair allowed) is placed immediately in the free chair.
    site = tiny_site(
        site_dict,
        physicians={"day": 5, "evening": 5, "night": 5},
        spaces={
            "acute_beds": 1,
            "resuscitation_bays": 1,
            "fast_track_chairs": 1,
            "hallway_spaces": 0,
        },
        eligibility={
            1: ["resuscitation_bays"],
            2: ["acute_beds"],
            3: ["acute_beds"],
            4: ["fast_track_chairs", "acute_beds"],
            5: ["fast_track_chairs"],
        },
    )
    pts = patients(
        (0, 3, False, False, False), (1, 2, False, False, False), (2, 4, False, False, False)
    )
    r = run_simulation(
        site, settings(), patients=pts, durations=durations(3, triage=1), bed_release_times=[]
    )
    assert row(r, 3).space_type == "fast_track_chairs" and row(r, 3).space_wait_min == 0
    assert row(r, 2).space_start_min == row(r, 1).departure_min  # waited for the acute bed


# --- Shift changes and midnight ---------------------------------------------------------


def test_new_shift_capacity_starts_waiting_patients(site_dict: dict[str, Any]) -> None:
    site = tiny_site(site_dict, physicians={"day": 1, "evening": 2, "night": 1})
    pts = patients((830, 3, False, False, False), (831, 3, False, False, False))  # 13:50
    r = run_simulation(
        site,
        settings(),
        patients=pts,
        durations=durations(2, triage=1, provider=120),
        bed_release_times=[],
    )
    assert row(r, 1).provider_start_min == 831
    assert row(r, 2).provider_start_min == 900  # second physician arrives at 15:00
    assert (900, 2) in list(
        zip(r.physicians.capacity_series.times, r.physicians.capacity_series.values, strict=True)
    )


def test_shift_end_never_interrupts_and_never_overassigns(site_dict: dict[str, Any]) -> None:
    # Evening has 2 physicians, night 1. Two exams start 22:30 and run to 23:30.
    site = tiny_site(
        site_dict,
        physicians={"day": 1, "evening": 2, "night": 1},
        triage_nurses={"day": 3, "evening": 3, "night": 3},
    )
    pts = patients(
        (1349, 3, False, False, False),
        (1349, 3, False, False, False),
        (1349, 3, False, False, False),
    )
    r = run_simulation(
        site,
        settings(),
        patients=pts,
        durations=durations(3, triage=1, provider=60),
        bed_release_times=[],
    )
    assert row(r, 1).provider_end_min == row(r, 2).provider_end_min == 1410  # past 23:00
    assert row(r, 3).provider_start_min == 1410  # only after BOTH finish (night cap = 1)
    assert all(busy <= cap for _, busy, cap in r.physicians.assignments)


def test_visit_crossing_midnight(site_dict: dict[str, Any]) -> None:
    site = tiny_site(site_dict)
    r = run_simulation(
        site,
        settings(days=2),
        patients=patients((1430, 3, True, False, False)),
        durations=durations(1),
        bed_release_times=[],
    )
    p = row(r, 1)
    assert p.arrival_time == pd.Timestamp(2026, 11, 16, 23, 50)
    assert p.departure_min > 1440 and p.los_min == 5 + 30 + 60 + 10
    # The same (night) shift covers both sides of midnight.
    assert r.physicians.capacity_at(1430) == r.physicians.capacity_at(1450) == 1


# --- Zero arrivals, saturation, unfinished visits ------------------------------------------


def test_zero_arrivals(tiny) -> None:  # noqa: ANN001
    r = run_simulation(tiny, settings(), patients=patients(), durations=[], bed_release_times=[])
    assert r.patients.empty and r.stop_reason == "all_departed" and r.stop_time == 0
    assert r.physicians.busy == 0 and r.spaces.queue_length == 0


def test_fully_utilized_physician(tiny) -> None:  # noqa: ANN001
    pts = patients(*[(i, 3, False, False, False) for i in range(30)])  # 30 x 60 min > 1 day
    r = run_simulation(
        tiny,
        settings(),
        patients=pts,
        durations=durations(30, triage=0.1, provider=60),
        bed_release_times=[],
    )
    busy = r.physicians.busy_series.integral(0, 1440)
    assert busy / 1440 > 0.99
    # Interacting queues: only 5 beds, so at most 4 patients wait for the
    # physician in a bed; everyone else waits UPSTREAM for a bed.
    assert r.physicians.queue_series.maximum(0, 1440) == 4
    assert r.spaces.queue_series.maximum(0, 1440) >= 20
    assert all(b <= c for _, b, c in r.physicians.assignments)
    assert r.stop_reason == "all_departed"  # drained after arrivals stopped


def test_hard_stop_keeps_unfinished_visits(tiny) -> None:  # noqa: ANN001
    pts = patients((0, 3, False, False, False), (1430, 3, False, False, False))
    r = run_simulation(
        tiny,
        settings(drain_hours=0),
        patients=pts,
        durations=durations(2, provider=60),
        bed_release_times=[],
    )
    assert r.stop_reason == "hard_stop" and r.stop_time == 1440
    assert len(r.patients) == 2  # nobody silently dropped
    late = row(r, 2)
    assert late.status == "unfinished" and late.stage_at_stop == "in_evaluation"
    assert late.time_in_ed_at_stop_min == 10 and pd.isna(late.los_min)
    codes = {f.code for f in run_checks(r)}
    assert {"hard_stop_reached", "unfinished_visits", "short_run"} <= codes


@pytest.mark.parametrize(
    ("minutes_before_stop", "stage"),
    [
        (1, "in_triage"),  # triage takes 2 min
        (20, "in_evaluation"),  # evaluation runs 2-32 after arrival
        (100, "awaiting_results"),  # lab runs 32-132 after arrival
        (200, "boarding"),  # no bed is ever released
    ],
)
def test_stage_at_stop(tiny, minutes_before_stop: float, stage: str) -> None:  # noqa: ANN001
    # With drain_hours=0 the hard stop is midnight; arrive just before it.
    pts = patients((1440 - minutes_before_stop, 2, True, False, True))
    r = run_simulation(
        tiny,
        settings(drain_hours=0),
        patients=pts,
        durations=durations(1, triage=2, provider=30, lab=100),
        bed_release_times=[],
    )
    assert row(r, 1).stage_at_stop == stage


def test_stage_waiting_for_triage_and_space(site_dict: dict[str, Any]) -> None:
    # One bed: P1 holds it; P2 finished triage but waits for the bed; P3 waits for triage.
    site = tiny_site(
        site_dict,
        spaces={
            "acute_beds": 1,
            "resuscitation_bays": 1,
            "fast_track_chairs": 0,
            "hallway_spaces": 0,
        },
    )
    pts = patients(
        (1300, 3, False, False, False),
        (1310, 3, False, False, False),
        (1438, 3, False, False, False),
        (1439, 3, False, False, False),
    )
    r = run_simulation(
        site,
        settings(drain_hours=0),
        patients=pts,
        durations=durations(4, triage=5, provider=400),
        bed_release_times=[],
    )
    assert row(r, 1).stage_at_stop == "in_evaluation"
    assert row(r, 2).stage_at_stop == "waiting_for_space"
    assert row(r, 4).stage_at_stop == "waiting_for_triage"


# --- Input validation ----------------------------------------------------------------------


def test_inputs_are_validated(tiny) -> None:  # noqa: ANN001
    with pytest.raises(SimulationError, match="missing columns"):
        run_simulation(tiny, settings(), patients=pd.DataFrame({"x": [1]}))
    with pytest.raises(SimulationError, match="outside the arrival window"):
        run_simulation(tiny, settings(), patients=patients((1500, 3, False, False, False)))
    with pytest.raises(SimulationError, match="sorted by arrival"):
        run_simulation(
            tiny,
            settings(),
            patients=patients((50, 3, False, False, False), (10, 3, False, False, False)),
        )
    with pytest.raises(SimulationError, match="duration sets"):
        run_simulation(
            tiny, settings(), patients=patients((5, 3, False, False, False)), durations=[]
        )
