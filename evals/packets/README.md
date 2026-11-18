# Frozen evidence packets (SIMULATED)

These are copies of evidence packets built by `build_evidence_packet()` from the
verified results bundle, frozen so the evaluation is reproducible even if the
simulation is re-run. They contain only fictional, simulated aggregates: no
patient-level records, no real hospital data, no personal data.

| File | Intervention | Focus outcome | Why it is in the set |
|---|---|---|---|
| `triage_nurse.json` | Add one night triage nurse | Triage wait, p90 (lower) | Opposite movements: physician wait and boarding p90 higher |
| `night_physician.json` | Add one night physician | Boarding, p90 (higher) | Counter-intuitive focus; sensitivity says the boarding direction depends on assumptions |
| `bed_releases.json` | Six more inpatient beds released per day | Boarding, p90 (lower, large) | Large effect; many outcomes with no detectable change; sensitivity stable |
| `evening_physician.json` | Add one evening physician | Boarding, p90 (no detectable change, wide CI) | Tests "absence of evidence" wording |
| `diagnostics.json` | Lab and imaging turnaround 25% faster | Discharged LOS, median (lower) | No opposite movement; a number inside the scenario name |
| `triage_nurse_tampered.json` | (copy of `triage_nurse`) | | **Deliberately tampered** for adversarial tests: an instruction-like sentence planted in `what_changes` and an evaluative word in a label, as if they had slipped past sanitization |

Rebuild the first five with `python -m beyond_the_wait.evaluation freeze-packets`
(this changes the evaluation inputs; re-run and re-document the results).
