"""Controlled vocabulary for the ACCEPT gate (Milestone 9).

The red-flag patterns in verify.py are a DENYLIST: they reject wording known to
be unsafe. Evaluation showed that a denylist does not generalize: on two blind
held-out rounds it missed 12/31 and then 9/20 unsafe responses phrased in ways
it had never seen ("the lever to pull", "why wait?", "no reason not to add").

This module adds an ALLOWLIST. A response can be ACCEPTED only if every word
of its prose is "vetted": a function word, a word from the instructions Claude
is given (the system prompt and schema), a word from the evidence itself
(labels, definitions, cautions, limitations), or a short list of neutral
descriptive words. Any other word sends the response to HUMAN_REVIEW, with the
unvetted words listed so a reviewer can look at exactly those sentences.

The allowlist never REJECTS anything on its own and never makes a response
"safe": vetted words can still be combined into a misleading sentence. It only
changes the default for unfamiliar wording from "accept" to "ask a human".

Modal verbs of obligation (should, must, ought, need) are deliberately not
vetted: in this domain they almost always introduce advice.
"""

from __future__ import annotations

import re
from functools import lru_cache

from beyond_the_wait.analysis.check_descriptions import CHECK_DESCRIPTIONS
from beyond_the_wait.analysis.experiments import MODEL_EVIDENCE_NOTE
from beyond_the_wait.analysis.validation import SAFETY_LIMITATIONS
from beyond_the_wait.interpretation.evidence import METRIC_DEFINITIONS, VERDICTS, EvidencePacket
from beyond_the_wait.interpretation.prompt import SYSTEM_PROMPT

FUNCTION_WORDS = """
a about above across after again against all also although am among an and any are as at
be been before being below between both but by can cannot could did do does doing down
during each either else even ever every few for from further had has have having he her
here hers him his how however i if in into is it its itself just least less many may me
might more most much my neither no nor not of off on once one only or other others our
out over own per same she since so some such than that the their them then there these
they this those through thus to too two under until up upon us very was we were what when
where whether which while who whom whose why will with within without would yet you your
three four five six seven eight nine ten zero first second third half
""".split()

# Obligation modals are never vetted (they introduce advice).
UNVETTED_ALWAYS = {"should", "must", "ought"}

# Neutral words for describing a comparison, chosen from the vocabulary of
# descriptive statistics and the project's own documentation, not from the
# evaluation cases.
DESCRIPTIVE_WORDS = """
model simulated simulation fictional baseline arm change comparison compare intervention
scenario outcome result value number difference direction lower higher shorter longer fewer
more less same different similar opposite detectable detectably measurable interval
confidence uncertainty uncertain average mean median percentile share proportion rate
percent percentage point minute hour week day patient wait time stay length visit stage queue
line physician nurse triage boarding board admitted discharged admission inpatient bed release
space treatment lab imaging turnaround diagnostic utilization workload busy evening night
overnight shift extra additional add another status caution limitation assumption assume
sensitivity setting check verdict test evidence packet fact report show describe explanation
generated human review reviewer decision support recommendation guidance clinical real ed
hospital department emergency data local measure measurement include item paired pair seed
random run consistent remain rose fell increase decrease reduce drop grew went came reach
finish start began end qualified people staff question investigate relevant resemble process
consider discussion illustrative abstraction simplified derived actual true depend dependent
unstable stable label labeled marked associated available several nothing none appropriate
demonstration mock output written exist pattern re
""".split()

# Common, neutral English words (a general high-frequency list, written without
# looking at the evaluation cases). Words that judge, advise, or assert causes
# are deliberately absent: better, worse, safe, benefit, improve, recommend,
# should, must, because, cause, prove, clear, obvious, strong, lever, case.
COMMON_WORDS = """
able about across actually after again ago almost alone along already always among amount
another answer anything appear apply area around ask asked away back based become began
behind being believe below beside beyond big bit both bring brought call came can cannot
certain chance close come coming common complete completely consider contain continue
could count course cover current currently describe detail determine develop did differ
difficult direct do does doing done during early easy effect either enough entire even
event exact exactly example except expect explain express fact fall far feel few find
following form found full general get give given go goes going gone group happen hard
held help high hold however idea important instead interest keep kind know known large
last late later lead least leave left let like likely little live long look lot low made
main make making manner many matter maybe mean means measure might mind miss moment move
much name narrow near nearly need never new next normal note notice now number observe
obtain occur often open order original otherwise part particular pass past place plain
point possible present probably provide put quite range rather read reason receive record
refer regard regardless relate remain remove repeat represent require rest return right
rule said say second see seem seen separate set several shape short side sign since single
size small sometimes soon sort source specific spend stand state step still such suggest
sum suppose sure take taken tell tend term than though thought together told took total
toward true try turn type typical unless usual usually variable various view way well
went whole wide word work worked write written yes yet
""".split()

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
_TOKEN = re.compile(r"\{\{[^}]*\}\}|\b[A-Z]{1,6}:[a-z0-9_]+\b")


def variants(word: str) -> set[str]:
    """Possible base forms of a word ('associated' -> associate, 'waits' -> wait)."""
    w = word.lower().strip("'-")
    if w.endswith("'s"):
        w = w[:-2]
    out = {w}
    for suffix, repls in (
        ("ies", ("y",)),
        ("ied", ("y",)),
        ("ing", ("", "e")),
        ("ed", ("", "e")),
        ("es", ("", "e")),
        ("s", ("",)),
        ("ly", ("", "le")),
        ("d", ("",)),
    ):
        if len(w) > len(suffix) + 2 and w.endswith(suffix):
            out |= {w[: -len(suffix)] + r for r in repls}
    if len(w) > 4 and w[-1] == w[-2]:  # 'dropped' -> 'dropp' -> 'drop'
        out.add(w[:-1])
    out |= {v[:-1] for v in list(out) if len(v) > 4 and v[-1] == v[-2]}
    return out


def words(text: str) -> list[str]:
    parts: list[str] = []
    for w in _WORD.findall(_TOKEN.sub(" ", text)):
        parts += [p for p in w.split("-") if p]
    return parts


def _vocab_from(texts: list[str]) -> set[str]:
    return {v for t in texts for w in words(t) for v in variants(w)}


@lru_cache(maxsize=1)
def base_vocabulary() -> frozenset[str]:
    from beyond_the_wait.interpretation.schema import Interpretation

    sources = [SYSTEM_PROMPT, MODEL_EVIDENCE_NOTE, *SAFETY_LIMITATIONS, *VERDICTS.values()]
    sources += list(METRIC_DEFINITIONS.values())
    sources += [f"{t} {d}" for t, d in CHECK_DESCRIPTIONS.values()]
    sources += [str(f.description or "") for f in Interpretation.model_fields.values()]
    vocab = _vocab_from(sources) | _vocab_from(FUNCTION_WORDS + DESCRIPTIVE_WORDS + COMMON_WORDS)
    return frozenset(vocab - UNVETTED_ALWAYS)


def packet_vocabulary(packet: EvidencePacket) -> frozenset[str]:
    texts = [packet.scenario_name, *packet.what_changes]
    for f in packet.facts:
        texts.append(getattr(f, "text", "") or f"{getattr(f, 'label', '')}")
    return frozenset(base_vocabulary() | (_vocab_from(texts) - UNVETTED_ALWAYS))


def unvetted(text: str, vocab: frozenset[str]) -> list[str]:
    """Words in `text` (tokens and fact IDs removed) that are not in the vocabulary."""
    out: list[str] = []
    for w in words(text):
        lw = w.lower()
        if lw in UNVETTED_ALWAYS or not (variants(w) & vocab):
            if lw not in out:
                out.append(lw)
    return out
