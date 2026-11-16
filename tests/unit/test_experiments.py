"""Tests for the multi-seed experiment engine.

Comparison logic is tested with HAND-BUILT replication records (exact,
deterministic). Integration tests run small real experiments.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any

import pandas as pd
import pytest

from beyond_the_wait.analysis import experiments as ex
from beyond_the_wait.analysis.experiments import (
    METRICS,
    Arm,
    ExperimentSettings,
    Replication,
    assumption_variant,
    binding_constraint_evidence,
    compare,
    comparison_table,
    congestion_shift,
    describe,
    run_arms,
    sensitivity_analysis,
    stability,
)
from beyond_the_wait.analysis.validation import GateStatus
from beyond_the_wait.config import Scenario, SiteProfile, load_scenario
from tests.paths import SCENARIOS_DIR

REAL_NAMES = ("lincoln", "barnabas", "jacobi", "montefiore", "bronxcare")


def _rep(
    arm: str,
    seed: int,
    *,
    status: GateStatus = GateStatus.CAUTION,
    overloaded: bool = False,
    cautions: tuple[str, ...] = ("single_seed",),
    **values: float,
) -> Replication:
    metrics = {m.name: 10.0 for m in METRICS} | {"unfinished_measured": 0.0} | values
    if status is GateStatus.BLOCKED:
        return Replication(arm, seed, status, overloaded, cautions, ("bad",), None)
    return Replication(arm, seed, status, overloaded, cautions, (), metrics)


def _arms(
    site: SiteProfile, kind: str = "intervention", effects: dict | None = None
) -> tuple[Arm, Arm]:
    eff = (
        effects
        if effects is not None
        else {"simulated": ["physicians +1"], "not_simulated": [], "assumptions": []}
    )
    return Arm("baseline", site), Arm("scen", site, kind=kind, effects=eff)


def _reps(
    n: int, base_values: dict[str, list[float]], scen_values: dict[str, list[float]], **kw: Any
):
    return {
        "baseline": [
            _rep("baseline", i, **{k: v[i] for k, v in base_values.items()}) for i in range(n)
        ],
        "scen": [
            _rep("scen", i, **{k: v[i] for k, v in scen_values.items()}, **kw) for i in range(n)
        ],
    }


# --- Paired difference logic (hand-built replications) ---------------------------------------


def test_paired_difference_and_ci_are_exact(site: SiteProfile) -> None:
    base, scen = _arms(site)
    reps = _reps(
        5,
        {"los_median_discharged": [100, 110, 120, 130, 140]},
        {"los_median_discharged": [99, 108, 117, 126, 135]},
    )
    mc = compare(base, scen, reps).metrics["los_median_discharged"]
    e = mc.estimate
    assert e.mean_difference == pytest.approx(-3.0)  # differences -1..-5
    assert e.ci_high == pytest.approx(-3 + 2.776445 * (2.5**0.5) / 5**0.5, abs=1e-5)
    assert mc.direction == "improves"


@pytest.mark.parametrize(
    ("metric", "base_vals", "scen_vals", "expected"),
    [
        ("los_median_discharged", [10, 11, 12, 13], [12, 13, 14, 15], "worsens"),
        ("los_median_discharged", [10, 11, 12, 13], [11, 10, 13, 12], "inconclusive"),
        ("los_median_discharged", [10, 11, 12, 13], [10, 11, 12, 13], "no_change"),
        ("physician_utilization", [0.7, 0.71, 0.72, 0.73], [0.6, 0.62, 0.63, 0.62], "changes"),
    ],
)
def test_direction_rules(
    site: SiteProfile, metric: str, base_vals: list, scen_vals: list, expected: str
) -> None:
    base, scen = _arms(site)
    rep = compare(base, scen, _reps(4, {metric: base_vals}, {metric: scen_vals}))
    assert rep.metrics[metric].direction == expected


# --- Gate integration -----------------------------------------------------------------------


def test_blocked_replication_invalidates_the_comparison(site: SiteProfile) -> None:
    base, scen = _arms(site)
    reps = _reps(5, {}, {})
    reps["scen"][2] = _rep("scen", 2, status=GateStatus.BLOCKED)
    rep = compare(base, scen, reps)
    assert rep.status is GateStatus.BLOCKED and rep.metrics == {}
    assert "blocked_replications" in {c.check for c in rep.checks}
    assert "BLOCKED" in describe(rep, "los_median_discharged")
    assert "No conclusion" in binding_constraint_evidence(rep, "los_median_discharged")


def test_mismatched_seeds_cannot_be_paired(site: SiteProfile) -> None:
    base, scen = _arms(site)
    reps = _reps(4, {}, {})
    reps["scen"][3] = dataclasses.replace(reps["scen"][3], seed=99)
    rep = compare(base, scen, reps)
    assert rep.status is GateStatus.BLOCKED and "seed_pairing" in {c.check for c in rep.checks}


def test_caution_warnings_are_carried_forward(site: SiteProfile) -> None:
    base, scen = _arms(site)
    reps = _reps(4, {}, {}, cautions=("single_seed", "esi1_timeliness"))
    rep = compare(base, scen, reps)
    assert rep.carried_cautions == {"single_seed": 8, "esi1_timeliness": 4}
    assert rep.status is GateStatus.CAUTION


def test_missing_values_are_counted_not_silently_dropped(site: SiteProfile) -> None:
    base, scen = _arms(site)
    nan = float("nan")
    reps = _reps(5, {"boarding_p90": [1, 2, nan, 4, 5]}, {"boarding_p90": [1, 2, 3, 4, 4]})
    rep = compare(base, scen, reps)
    assert (
        rep.metrics["boarding_p90"].n_missing == 1 and rep.metrics["boarding_p90"].estimate.n == 4
    )
    check = next(c for c in rep.checks if c.check == "missing_data")
    assert check.status is GateStatus.CAUTION and "boarding_p90: 1" in check.detail


def test_unfinished_visits_trigger_censoring_caution(site: SiteProfile) -> None:
    base, scen = _arms(site)
    rep = compare(base, scen, _reps(4, {}, {"unfinished_measured": [0, 3, 0, 1]}))
    check = next(c for c in rep.checks if c.check == "censoring")
    assert check.status is GateStatus.CAUTION and "lower_bound" in check.detail
    assert rep.metrics["unfinished_measured"].direction in ("inconclusive", "worsens")


def test_overloaded_replications_are_labeled_stress_regime(site: SiteProfile) -> None:
    base, scen = _arms(site)
    rep = compare(base, scen, _reps(4, {}, {}, overloaded=True))
    check = next(c for c in rep.checks if c.check == "overload_regime")
    assert check.status is GateStatus.CAUTION and "STRESS-TEST" in check.detail


def test_seed_coverage(site: SiteProfile) -> None:
    base, scen = _arms(site)
    few = compare(base, scen, _reps(5, {}, {}))
    many = compare(base, scen, _reps(30, {}, {}))
    assert next(c for c in few.checks if c.check == "seed_coverage").status is GateStatus.CAUTION
    assert next(c for c in many.checks if c.check == "seed_coverage").status is GateStatus.PASS


def test_noisy_and_skewed_estimates_flagged(site: SiteProfile) -> None:
    base, scen = _arms(site)
    # One large outlier among 12 pairs: skewness ~3, and a wide interval relative to 10.
    rep = compare(
        base, scen, _reps(12, {"boarding_p90": [10] * 12}, {"boarding_p90": [10] * 11 + [60]})
    )
    checks = {c.check: c for c in rep.checks}
    assert "boarding_p90" in checks["excessive_variability"].detail
    assert "boarding_p90" in checks["skewed_differences"].detail


@pytest.mark.parametrize(
    ("kind", "effects", "phrase"),
    [
        ("sensitivity", None, "not intervention effects"),
        (
            "intervention",
            {"simulated": [], "not_simulated": ["nurses +2 on day shift"], "assumptions": []},
            "no simulation effect",
        ),
    ],
)
def test_unsupported_conclusions_flagged(
    site: SiteProfile, kind: str, effects: dict | None, phrase: str
) -> None:
    base, scen = _arms(site, kind=kind, effects=effects)
    rep = compare(base, scen, _reps(4, {}, {}))
    detail = next(c for c in rep.checks if c.check == "unsupported_conclusions").detail
    assert phrase in detail and "fictional model" in detail


def test_congestion_shift_detects_relocated_waiting(site: SiteProfile) -> None:
    base, scen = _arms(site)
    reps = _reps(
        4,
        {"stage_triage_wait_mean": [10, 11, 12, 13], "stage_provider_wait_mean": [5, 5, 6, 6]},
        {"stage_triage_wait_mean": [5, 6, 7, 8], "stage_provider_wait_mean": [9, 9, 10, 11]},
    )
    shift = congestion_shift(compare(base, scen, reps))
    assert shift == {
        "improves": ["stage_triage_wait_mean"],
        "worsens": ["stage_provider_wait_mean"],
        "moves_congestion": ["True"],
    }


def test_text_is_always_scoped_to_the_fictional_model(site: SiteProfile) -> None:
    base, scen = _arms(site)
    rep = compare(
        base,
        scen,
        _reps(
            4,
            {"los_median_admitted": [300, 310, 320, 330]},
            {"los_median_admitted": [200, 205, 230, 240]},
        ),
    )
    texts = [describe(rep, m.name) for m in METRICS] + [
        binding_constraint_evidence(rep, "los_median_admitted"),
        binding_constraint_evidence(rep, "los_median_discharged"),
        rep.evidence_note,
    ]
    for t in texts:
        assert not any(name in t.lower() for name in REAL_NAMES)
    assert all(t.startswith("Within the fictional model") for t in texts[:-1])
    assert "evidence that this constraint was binding" in texts[-3]
    assert "not supported" in texts[-2]
    assert "not evidence about any real hospital" in rep.evidence_note
    table = comparison_table([rep], ["los_median_admitted"])
    assert (table["provenance"] == "SIMULATED").all()


# --- Settings ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"seeds": (1,)}, "at least 2"),
        ({"seeds": (1, 1)}, "unique"),
        ({"seeds": (1, -2)}, "non-negative"),
        ({"seeds": (1, 2), "warmup_days": 8}, "warmup_days"),
    ],
)
def test_experiment_settings_validation(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ExperimentSettings(start_date=date(2026, 11, 16), **kwargs)


# --- Integration: small real experiments -----------------------------------------------------

SMALL = ExperimentSettings(
    start_date=date(2026, 11, 16), arrival_days=3, warmup_days=1, seeds=(1, 2, 3, 4)
)


@pytest.fixture(scope="module")
def real(request: pytest.FixtureRequest):  # noqa: ANN201
    from beyond_the_wait.config import load_site_profile
    from tests.paths import EXAMPLE_SITE_PATH

    site = load_site_profile(EXAMPLE_SITE_PATH)
    base = Arm("baseline", site)
    evening = Arm.from_scenario(base, load_scenario(SCENARIOS_DIR / "add_evening_physician.yaml"))
    return base, evening, run_arms([base, evening], SMALL)


def test_same_seeds_same_results(real) -> None:  # noqa: ANN001
    base, evening, reps = real
    again = run_arms([base, evening], SMALL)
    assert [r.metrics for r in again["baseline"]] == [r.metrics for r in reps["baseline"]]


def test_parallel_equals_serial(real) -> None:  # noqa: ANN001
    base, evening, reps = real
    parallel = run_arms([base, evening], SMALL.model_copy(update={"workers": 2}))
    assert [r.metrics for r in parallel["add_evening_physician"]] == [
        r.metrics for r in reps["add_evening_physician"]
    ]


def test_real_comparison_uses_common_random_numbers(real) -> None:  # noqa: ANN001
    base, evening, reps = real
    rep = compare(base, evening, reps)
    assert rep.status is GateStatus.CAUTION
    assert rep.metrics["measured_patients"].direction == "no_change"  # same patients in both arms
    assert rep.metrics["physician_utilization"].direction == "changes"
    assert rep.metrics["physician_utilization"].estimate.mean_difference < 0


def test_blocked_real_replication_stops_comparison(real, monkeypatch) -> None:  # noqa: ANN001
    base, evening, _ = real
    original = ex.run_simulation

    def corrupt(site, settings):  # noqa: ANN001, ANN202
        r = original(site, settings)
        if site.staffing.physicians_on_duty["evening"] == 5 and settings.seed == 2:
            return dataclasses.replace(r, patients=r.patients.assign(provenance="PUBLIC"))
        return r

    monkeypatch.setattr(ex, "run_simulation", corrupt)
    reps = run_arms([base, evening], SMALL)
    blocked = [r for r in reps["add_evening_physician"] if r.status is GateStatus.BLOCKED]
    assert len(blocked) == 1 and blocked[0].metrics is None  # no numbers kept from a BLOCKED run
    assert compare(base, evening, reps).status is GateStatus.BLOCKED


def test_assumption_variant(site: SiteProfile) -> None:
    same = assumption_variant(site, concurrency=2, releases_per_day=42.0)
    assert same.site is site and same.kind == "baseline"
    v = assumption_variant(site, concurrency=3, releases_per_day=46.0)
    assert v.site.operations.physician_concurrent_patients == 3
    assert v.site.operations.inpatient_bed_availability.releases_per_day == 46
    assert v.kind == "sensitivity"


def test_sensitivity_analysis_small(site: SiteProfile) -> None:
    sc = load_scenario(SCENARIOS_DIR / "increase_bed_releases.yaml")
    tiny = SMALL.model_copy(update={"seeds": (1, 2, 3)})
    df = sensitivity_analysis(
        site,
        [sc],
        tiny,
        concurrency_values=(1, 2),
        release_values=(42.0,),
        metrics=("boarding_p90",),
    )
    assert len(df) == 2 and set(df["provenance"]) == {"SIMULATED"}
    assert df.set_index("concurrency").loc[1, "regime"] == "stress_test"


def test_stability_verdicts() -> None:
    def rows(dirs: list[str], name: str) -> list[dict]:
        return [
            {"intervention": name, "metric": "m", "direction": d, "regime": "operating"}
            for d in dirs
        ]

    df = pd.DataFrame(
        rows(["improves", "improves"], "a")
        + rows(["improves", "worsens"], "b")
        + rows(["improves", "inconclusive"], "c")
        + rows(["inconclusive"], "d")
        + [{"intervention": "a", "metric": "m", "direction": "worsens", "regime": "stress_test"}]
    )
    verdicts = stability(df).set_index("intervention")["verdict"].to_dict()
    assert verdicts == {"a": "stable_improves", "b": "reverses", "c": "unstable", "d": "no_support"}
    assert stability(df, regime=None).set_index("intervention").loc["a", "verdict"] == "reverses"


def test_whatif_demo_small_run(capsys: pytest.CaptureFixture[str], tmp_path) -> None:  # noqa: ANN001
    from beyond_the_wait.analysis import whatif_demo

    code = whatif_demo.main(
        [
            "--seeds",
            "2",
            "--days",
            "2",
            "--workers",
            "1",
            "--skip-sensitivity",
            "--out",
            str(tmp_path),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    for text in (
        "BASELINE vs INTERVENTIONS",
        "FICTIONAL ARCHETYPES",
        "INSIDE THE FICTIONAL MODEL",
        "Model-internal evidence only",
        "NOT A STAFFING RECOMMENDATION",
    ):
        assert text in out
    assert not any(name in out.lower() for name in REAL_NAMES)
    assert (tmp_path / "main_comparison.csv").is_file() and (tmp_path / "archetypes.csv").is_file()


def test_whatif_demo_rejects_bad_settings(capsys: pytest.CaptureFixture[str]) -> None:
    from beyond_the_wait.analysis import whatif_demo

    assert whatif_demo.main(["--seeds", "1"]) == 1


def test_scenario_kinds_loaded(site: SiteProfile) -> None:
    kinds = {load_scenario(p).kind for p in SCENARIOS_DIR.glob("*.yaml")}
    assert kinds == {"intervention", "archetype", "sensitivity"}
    assert isinstance(load_scenario(SCENARIOS_DIR / "archetype_boarding_heavy.yaml"), Scenario)


def test_all_missing_metric_is_unavailable_and_described_honestly(site: SiteProfile) -> None:
    base, scen = _arms(site)
    nan = float("nan")
    rep = compare(base, scen, _reps(3, {"boarding_p90": [nan] * 3}, {"boarding_p90": [nan] * 3}))
    assert rep.metrics["boarding_p90"].direction == "unavailable"
    assert "unavailable" in describe(rep, "boarding_p90")
    assert "unavailable" in binding_constraint_evidence(rep, "boarding_p90")


def test_worsening_evidence_text(site: SiteProfile) -> None:
    base, scen = _arms(site)
    rep = compare(
        base, scen, _reps(4, {"boarding_p90": [10, 11, 12, 13]}, {"boarding_p90": [20, 22, 23, 25]})
    )
    text = binding_constraint_evidence(rep, "boarding_p90")
    assert text.startswith("Within the fictional model") and "worsened" in text


def test_stress_test_scenario_arm_uses_stress_intent(site: SiteProfile) -> None:
    sc = Scenario.model_validate(
        {
            "schema_version": 2,
            "scenario_id": "overload_test",
            "kind": "stress_test",
            "name": "overload",
            "description": "fictional overload stress test",
            "applies_to_site": "riverside_commons_ed",
            "physician_concurrency_override": 1,
        }
    )
    arm = Arm.from_scenario(Arm("baseline", site), sc)
    assert arm.intent == "stress_test" and arm.effects["assumptions"]


def test_demo_prints_blocked_comparisons_without_numbers(
    site: SiteProfile, capsys: pytest.CaptureFixture[str]
) -> None:
    from beyond_the_wait.analysis import whatif_demo

    base, scen = _arms(site)
    reps = _reps(3, {}, {})
    reps["scen"][0] = _rep("scen", 0, status=GateStatus.BLOCKED)
    whatif_demo._print_comparison(compare(base, scen, reps), ("los_median_discharged",))
    out = capsys.readouterr().out
    assert "BLOCKED" in out and "LOS discharged" not in out


def test_whatif_demo_sensitivity_section(capsys: pytest.CaptureFixture[str], tmp_path) -> None:  # noqa: ANN001
    from beyond_the_wait.analysis import whatif_demo

    code = whatif_demo.main(
        [
            "--seeds",
            "2",
            "--sensitivity-seeds",
            "2",
            "--days",
            "2",
            "--workers",
            "1",
            "--skip-archetypes",
            "--out",
            str(tmp_path),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0 and "SENSITIVITY to fictional assumptions" in out and "Stability across" in out
    assert (tmp_path / "sensitivity.csv").is_file() and (tmp_path / "stability.csv").is_file()


def test_demo_prints_unavailable_metrics_and_congestion_moves(
    site: SiteProfile, capsys: pytest.CaptureFixture[str]
) -> None:
    from beyond_the_wait.analysis import whatif_demo

    base, scen = _arms(site)
    nan = float("nan")
    reps = _reps(
        4,
        {
            "boarding_p90": [nan] * 4,
            "stage_triage_wait_mean": [10, 11, 12, 13],
            "stage_provider_wait_mean": [5, 5, 6, 6],
        },
        {
            "boarding_p90": [nan] * 4,
            "stage_triage_wait_mean": [5, 6, 7, 8],
            "stage_provider_wait_mean": [9, 9, 10, 11],
        },
    )
    whatif_demo._print_comparison(compare(base, scen, reps), ("boarding_p90",))
    out = capsys.readouterr().out
    assert "n/a" in out and "CONGESTION MOVES" in out


def test_demo_archetype_section_reports_congestion_moves(
    monkeypatch, capsys: pytest.CaptureFixture[str], tmp_path
) -> None:  # noqa: ANN001
    from beyond_the_wait.analysis import whatif_demo

    monkeypatch.setattr(
        whatif_demo,
        "congestion_shift",
        lambda rep: {"improves": ["a"], "worsens": ["b"], "moves_congestion": ["True"]},
    )
    whatif_demo.main(
        [
            "--seeds",
            "2",
            "--days",
            "2",
            "--workers",
            "1",
            "--skip-sensitivity",
            "--out",
            str(tmp_path),
        ]
    )
    assert capsys.readouterr().out.count("CONGESTION MOVES") >= 9  # 5 interventions + 4 archetypes
