"""Supervised live evaluation of Claude explanations: plan, guarded batch, blind review.

Offline by default. The only code path that can reach the Claude API is
`batch.live_provider`, used solely by `python -m beyond_the_wait.live_eval
generate --mode live`, which requires the plan fingerprint as an approval code,
an API key, an interactive terminal, and a typed confirmation. Every record is
written under evals/results/live/ (git-ignored) and never shown in the dashboard.
"""
