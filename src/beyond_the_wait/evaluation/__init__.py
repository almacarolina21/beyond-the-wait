"""Offline evaluation of the AI interpretation guardrails (Milestone 9).

Everything here is deterministic and offline: candidate responses are
hand-written test data, scored by the same verification and faithfulness code
the dashboard uses. This package never imports an API client or a provider,
so running the evaluation cannot make a network call or incur charges.
"""
