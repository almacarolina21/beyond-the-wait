# 90-Second Demo Outline

To narrate yourself over a screen recording of the local dashboard. Start it
with `streamlit run app/streamlit_app.py` (DEMO mode, no API key).

Everything shown is real application output. The AI explanation is the
built-in **mock**, labeled "DEMO · mock, not Claude" on screen. Say so out
loud. Do not describe it as Claude's output.

| Time | Screen | What to say (in your own words) |
|---|---|---|
| 0:00–0:12 | **Overview**, top | "Bronx EDs have some of the longest waits in public data, but that data can't say *why*. This lab tests what-if changes on a **fictional** ED and keeps every number in tested Python." |
| 0:12–0:27 | **Overview**, the three finding cards | "Each result is 30 paired simulated weeks with a 95% interval. The third card is the interesting one: a night triage nurse cuts triage waits, but physician waits go up. The wait moved, and the tool flags that automatically." |
| 0:27–0:45 | **What-If Lab**, the interval chart (default outcome: arrival to provider, p90) | "Blue intervals exclude zero, gray ones don't. Everything is labeled SIMULATED and carries its validation status. A BLOCKED run is never shown." |
| 0:45–1:05 | **What-If Lab**, click **Explain these results** | "The explanation layer gets a typed evidence packet, never patient records. The model writes placeholders, and Python inserts every number. This is the **mock**, not Claude. Eleven checks passed, so it's shown. Text that needs human review is withheld from this page entirely." |
| 1:05–1:22 | **AI Explanation Evaluation**, blind results table | "I evaluated the checks on 180 hand-written explanations, with held-out rounds locked before scoring. Patching phrases failed: 61%, then 55%. Changing the default so that unfamiliar wording goes to a person reached 12 of 13. Those are the blind numbers, not the post-fix ones." |
| 1:22–1:30 | Back to the **Overview** header | "Python calculates, Claude explains, humans decide. Next is a capped, 10-call live evaluation with blind human review, which hasn't been run yet." |

**Before recording:**
- Use a fresh browser window at about 1440 × 900, in light mode.
- Close the sidebar's developer menu.
- Do not show a terminal holding environment variables or keys.

**Do not claim:** that the explanation came from Claude, that the system is
validated or deployed, or that any finding applies to a real hospital.
