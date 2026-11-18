"""Batch summary: human vs automated verdicts, errors, tokens, and cost."""

from __future__ import annotations

import json
from pathlib import Path

from beyond_the_wait.interpretation.faithfulness import VERDICTS
from beyond_the_wait.live_eval.batch import read_ledger


def _load(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def summarize(directory: Path) -> dict:
    meta = json.loads((directory / "batch.json").read_text())
    rows = []
    for s in meta["slots"]:
        rec = _load(directory / "responses" / f"{s['slot']}.json")
        manual = _load(directory / "reviews" / f"{s['slot']}.manual.json")
        auto = _load(directory / "reviews" / f"{s['slot']}.automated.json")
        rows.append({"plan": s, "record": rec, "manual": manual, "auto": auto})
    reviewed = [r for r in rows if r["manual"] and r["auto"]]
    confusion = {h: dict.fromkeys(VERDICTS, 0) for h in VERDICTS}
    for r in reviewed:
        confusion[r["manual"]["reviewer_verdict"]][r["auto"]["automated_verdict"]] += 1
    recs = [r["record"] for r in rows if r["record"]]
    return {
        "meta": meta,
        "rows": rows,
        "attempts": sum(1 for e in read_ledger(directory) if e["event"] == "attempt"),
        "responses": sum(1 for r in recs if r["status"] == "response"),
        "errors": sum(1 for r in recs if r["status"] == "error"),
        "reviewed": len(reviewed),
        "agree": sum(1 for r in reviewed if r["auto"]["agree"]),
        "automated_false_accepts": [
            r["plan"]["slot"] for r in reviewed if r["auto"]["automated_false_accept"]
        ],
        "confusion": confusion,
        "input_tokens": sum(r["usage"]["input_tokens"] for r in recs),
        "output_tokens": sum(r["usage"]["output_tokens"] for r in recs),
        "estimated_cost_usd": round(sum(r["estimated_cost_usd"] for r in recs), 4),
        "usage_simulated": any(r["usage_simulated"] for r in recs),
    }


def to_markdown(directory: Path) -> str:
    s = summarize(directory)
    meta = s["meta"]
    mock = meta["mode"] == "MOCK"
    lines = [
        f"# Live evaluation batch `{meta['batch_id']}` ({meta['mode']})",
        "",
        "> MOCK dry run: scripted stand-in responses and pre-written reviewer answers. "
        "Nothing here is Claude output or a real human judgment."
        if mock
        else "> LIVE batch: real Claude output on fictional, simulated evidence. "
        "Not clinical evidence; local and git-ignored.",
        "",
        f"- Model planned: `{meta['model']}` · prompt `{meta['prompt_version']}` "
        f"(sha256 {meta['prompt_sha256'][:12]}) · plan {meta['plan_sha256'][:12]}",
        f"- Calls attempted: {s['attempts']}/{meta['max_calls']} · responses {s['responses']} · "
        f"API errors {s['errors']} · reviewed {s['reviewed']}",
        f"- Tokens: {s['input_tokens']:,} in, {s['output_tokens']:,} out"
        + (" (simulated)" if s["usage_simulated"] else "")
        + f" · estimated cost ${s['estimated_cost_usd']:.4f} at list price",
        "- Billed (from the Console): "
        + (
            f"${meta['billing']['usd']:.2f} ({meta['billing']['source']})"
            if meta.get("billing")
            else "not recorded"
        ),
        f"- Human/automated agreement: {s['agree']}/{s['reviewed']}",
        "- **Automated false accepts (human REJECT, code ACCEPT): "
        + (", ".join(s["automated_false_accepts"]) or "none")
        + "**",
        "",
        "| Slot | Packet | Gen | Status | Human | Automated | Public | Tokens in/out | Est. $ "
        "| Error categories |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in s["rows"]:
        p, rec, man, auto = r["plan"], r["record"], r["manual"], r["auto"]
        status = (
            "not run"
            if rec is None
            else rec["status"]
            + (f" ({rec['error_kind']})" if rec and rec["status"] == "error" else "")
        )
        lines.append(
            f"| {p['slot']} | {p['packet']} | {p['generation']} | {status} | "
            f"{man['reviewer_verdict'] if man else '-'} | "
            f"{auto['automated_verdict'] if auto else '-'} | "
            f"{auto['public_display'] if auto else '-'} | "
            + (
                f"{rec['usage']['input_tokens']:,}/{rec['usage']['output_tokens']:,} | "
                f"{rec['estimated_cost_usd']:.4f} | "
                if rec
                else "- | - | "
            )
            + (", ".join(man["error_categories"]) if man else "-")
            + " |"
        )
    lines += [
        "",
        "Human (rows) vs automated (columns):",
        "",
        "| | " + " | ".join(VERDICTS) + " |",
        "|---|" + "---|" * len(VERDICTS),
    ]
    for h in VERDICTS:
        lines.append(f"| {h} | " + " | ".join(str(s["confusion"][h][a]) for a in VERDICTS) + " |")
    lines += [
        "",
        "Ten responses can reveal failure types, not reliable rates. API errors may still be "
        "billed; the Console usage page is the authority for actual charges.",
    ]
    return "\n".join(lines)


def record_billing(directory: Path, usd: float, source: str) -> None:
    """Store the actual charge read from the Anthropic Console (entered by a person)."""
    if usd < 0:
        raise ValueError("billed amount cannot be negative")
    path = directory / "batch.json"
    meta = json.loads(path.read_text())
    meta["billing"] = {"usd": usd, "source": source[:200]}
    path.write_text(json.dumps(meta, indent=1) + "\n")
