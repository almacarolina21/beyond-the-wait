"""Deterministic verification of an interpretation BEFORE anything is displayed.

Claude's reply is untrusted text until every check below passes. A response
that fails ANY check is rejected as a whole; nothing partly valid is shown.

What these checks CAN establish
  * The reply is valid JSON with the required fields and size limits.
  * Every cited evidence ID exists in the packet, and value tokens only use
    facts their statement cites.
  * Every number typed into the prose matches a packet value (within display
    rounding) with a compatible unit, so fabricated values and altered units
    (minutes shown as hours, percentage points shown as percent) are caught.
  * Direction words agree with the packet for single-outcome statements, and
    "no detectable change" outcomes are not described as changes.
  * Known red-flag patterns are absent: real hospital names, real-world causal
    claims, recommendations, safety/efficacy claims, clinical directives,
    misreadings of the 95% CI, URLs and invented citations, markup.
  * Required content is present: CAUTION status kept, limitations cited,
    uncertainties, questions, and a disclaimer.

What they CANNOT establish
  * That a cited fact actually supports the sentence citing it (semantic
    entailment). A reply can cite M:boarding_p90 and still misdescribe it in
    words the patterns do not catch.
  * That pattern lists are complete. Paraphrases, spelled-out numbers in
    unusual forms, or new phrasings of advice can slip through; failures are
    conservative (false alarms reject a good reply) but misses are possible.
  * That the explanation is useful, balanced, or clinically safe. Schema
    validity is not semantic correctness. Human review remains required.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from beyond_the_wait.interpretation.evidence import EvidencePacket, MetricFact, TextFact
from beyond_the_wait.interpretation.schema import Interpretation

MAX_RESPONSE_CHARS = 20_000
TOKEN_FIELDS = ("baseline", "intervention", "difference", "ci95", "change")
TOKEN = re.compile(r"\{\{\s*([A-Z]+:[a-z0-9_]+)\.([a-z0-9_]+)\s*\}\}")
FACT_ID = re.compile(r"\b[A-Z]{1,6}:[a-z0-9_]+\b")

# ---------------------------------------------------------------------------
# Rendering (numbers always come from the packet, never from Claude's text)
# ---------------------------------------------------------------------------


def _num(v: float) -> str:
    v = abs(v)
    return f"{v:.2f}" if v < 1 else f"{v:.1f}"


def _signed(v: float) -> str:
    return ("+" if v > 0 else "−" if v < 0 else "") + _num(v)


def _with_unit(text: str, unit: str) -> str:
    return f"{text}%" if unit == "%" else f"{text} {unit}"


def render_value(fact: MetricFact, fld: str) -> str:
    """Exact packet value for a token, formatted by Python."""
    if fld == "baseline":
        return _with_unit(_num(fact.baseline), fact.level_unit)
    if fld == "intervention":
        return _with_unit(_num(fact.intervention), fact.level_unit)
    if fld == "difference":
        return _with_unit(_signed(fact.difference), fact.change_unit)
    lo, hi = fact.ci95_low, fact.ci95_high
    ci = f"95% CI {_signed(lo)} to {_signed(hi)} {fact.change_unit}"
    if fld == "ci95":
        return ci
    # fld == "change": the full plain-language phrase
    if fact.direction.startswith("lower") or fact.direction.startswith("higher"):
        word = "lower" if fact.direction.startswith("lower") else "higher"
        a, b = sorted((abs(lo), abs(hi)))
        unit = fact.change_unit
        return (
            f"{_num(fact.difference)} {unit} {word} in the model "
            f"(95% CI {_num(a)} to {_num(b)} {unit} {word})"
        )
    if fact.direction.startswith("identical"):
        return "identical in both arms of the model"
    return f"no detectable change in the model ({ci})"


def render(text: str, packet: EvidencePacket) -> str:
    """Replace value tokens with packet values. Call only on verified text."""

    def sub(m: re.Match[str]) -> str:
        fact = packet.fact(m.group(1))
        if not isinstance(fact, MetricFact) or m.group(2) not in TOKEN_FIELDS:
            return "[unavailable]"
        return render_value(fact, m.group(2))

    return TOKEN.sub(sub, text)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class CheckOutcome:
    name: str
    description: str
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems


@dataclass
class VerificationReport:
    checks: list[CheckOutcome]
    interpretation: Interpretation | None = None

    @property
    def passed(self) -> bool:
        return self.interpretation is not None and all(c.passed for c in self.checks)

    @property
    def problems(self) -> list[str]:
        return [f"{c.name}: {p}" for c in self.checks for p in c.problems]


# ---------------------------------------------------------------------------
# Number extraction
# ---------------------------------------------------------------------------

_UNIT_CLASSES = [
    (r"percentage[- ]points?|pp\b|points?\b", "pp"),
    (r"%|percent\b", "pct"),
    (r"minutes?\b|mins?\b", "min"),
    (r"hours?\b|hrs?\b|h\b", "hour"),
    (r"seconds?\b|secs?\b", "sec"),
    (r"days?\b", "day"),
    (r"weeks?\b", "week"),
    (r"(?:simulated |paired )?(?:patients?|beds?|nurses?|physicians?|seeds?|settings?)\b", "count"),
]
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.])[-−+]?(\d+(?:\.\d+)?)\s*(" + "|".join(p for p, _ in _UNIT_CLASSES) + r")?",
    re.IGNORECASE,
)
_CI_PHRASE = re.compile(r"95\s*%\s*(CI\b|confidence)|95 percent confidence", re.IGNORECASE)
_SPELLED = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)\b[\s-]*(\w+[\s-]+)?"
    r"(minutes?|hours?|percent|percentage points?)\b",
    re.IGNORECASE,
)
_RELATIVE = re.compile(
    r"\b(twice|double[sd]?|halve[sd]?|in half|half|triple[sd]?|\d+(\.\d+)?\s*(x|times|-?fold)|"
    r"(a|one|two)\s+(quarter|third|fifth|tenth)s?|quarter|percent(age)?\s+(drop|cut|reduction|"
    r"increase|rise))\b",
    re.IGNORECASE,
)


def _unit_class(unit: str | None) -> str | None:
    if not unit:
        return None
    for pattern, cls in _UNIT_CLASSES:
        if re.fullmatch(pattern, unit.strip(), re.IGNORECASE):
            return cls
    return None  # pragma: no cover - every captured unit matches a class


def normalize(text: str) -> str:
    """Text with value tokens, fact IDs and the '95% CI' phrase removed (not numbers)."""
    return _CI_PHRASE.sub("CI", FACT_ID.sub(" ", TOKEN.sub(" ", text)))


def extract_numbers(text: str) -> list[tuple[float, str | None, int]]:
    """(value, unit class or None, end position in normalize(text)) for typed numbers."""
    text = normalize(text)
    return [(float(m.group(1)), _unit_class(m.group(2)), m.end()) for m in _NUMBER.finditer(text)]


def _fact_unit(unit: str) -> str:
    return {"%": "pct", "percentage points": "pp", "min": "min"}.get(unit, "count")


def _approved(facts: list[MetricFact | TextFact]) -> tuple[set[tuple[float, str]], set[float]]:
    """(value, unit-class) pairs and bare values the given facts make available."""
    with_unit: set[tuple[float, str]] = set()
    bare: set[float] = set()
    for f in facts:
        if isinstance(f, MetricFact):
            for v in (f.baseline, f.intervention):
                with_unit.add((abs(v), _fact_unit(f.level_unit)))
            for v in (f.difference, f.ci95_low, f.ci95_high):
                with_unit.add((abs(v), _fact_unit(f.change_unit)))
            with_unit.add((float(f.n_pairs), "count"))
            texts = [f.label, f.definition]
        else:
            texts = [f.text]
        for t in texts:
            for v, cls, _ in extract_numbers(t):
                bare.add(abs(v))
                if cls:
                    with_unit.add((abs(v), cls))
    bare |= {v for v, _ in with_unit}
    return with_unit, bare


def _matches(x: float, v: float) -> bool:
    x, v = abs(x), abs(v)
    return abs(x - v) <= 0.051 or (x == int(x) and abs(x - v) < 0.5)


# ---------------------------------------------------------------------------
# Language patterns
# ---------------------------------------------------------------------------

REAL_HOSPITALS = re.compile(
    r"montefiore|jacobi|lincoln|bronx\s*care|bronx[- ]lebanon|barnabas|\bsbh\b|"
    r"north central bronx|calvary|bronx va\b|weiler|einstein|wakefield|westchester square|"
    r"hutchinson|health\s*\+\s*hospitals|\bh\+h\b",
    re.IGNORECASE,
)
REAL_WORLD = re.compile(
    r"\b(bronx|new york|nyc|real[- ]world|real (hospital|ed|eds|emergency|patient|staff)\w*|"
    r"actual (hospital|ed|eds|patient)\w*|in practice|your (hospital|ed|emergency department)|"
    r"in reality|reality|urban|rural|nationally|national|across the country|other (eds|hospitals)|"
    r"most (eds|hospitals)|typical (eds|hospitals)|elsewhere|generali[sz]\w*)",
    re.IGNORECASE,
)
CAUSAL = re.compile(
    r"\b(because|due to|caused by|causes?|driven by|explains? why|the reason|root cause)\b",
    re.IGNORECASE,
)
RECOMMEND = re.compile(
    r"\b(recommend\w*|advis\w*|urge\w*|we suggest|i suggest|you should|consider (hiring|adding|"
    r"staffing|implementing|moving)|(should|must|ought to|needs? to)\s+([\w-]+\s+){0,3}?"
    r"(hire|add|staff|implement|adopt|deploy|invest|reduce|increase|cut|move|reassign|schedule|prioriti[sz]e|discharge|"
    r"admit|treat|triage|expand|fund|purchase|open|close|roll out|pilot))\b|"
    r"\b(would be (wise|prudent|sensible|advisable)|(is|are|was) (the )?(right|smart|wise) "
    r"(call|choice|move|decision)|good (investment|idea|choice|option|bet)|go ahead|"
    r"worth (it|doing|trying|considering|pursuing)|no downside|low[- ]risk|risk[- ]free|harmless|"
    r"makes? sense to|move forward with|opt for|go with|the way to go|prioriti[sz]e|"
    r"(it is|it's) (advisable|prudent|sensible)|justif\w*|(strong |good )?case for|encourag\w*|"
    r"proven|what (this|the) ed (lacks|needs)|lacks|understaffed|needs more)\b",
    re.IGNORECASE,
)
# Outcomes the model never measures: claiming them is unsupported.
UNMEASURED = re.compile(
    r"\b(mortality|deaths?|die|dying|lives|harm\w*|morbidity|adverse events?|patient outcomes?|"
    r"clinical outcomes?|quality of care|satisfaction|errors?|complications?|sicker|"
    r"care sooner|faster care|timely care|treated sooner|leaves? without being seen|"
    r"left without being seen|lwbs|walk[- ]?outs?)\b",
    re.IGNORECASE,
)
# Implementation and future framing: the model cannot forecast a real deployment.
FUTURE = re.compile(
    r"\b(will|expect\w*|once (implemented|adopted|in place)|after (implementing|adopting|"
    r"adding|hiring)|if (implemented|adopted))\b",
    re.IGNORECASE,
)
# Overclaiming certainty or naming a cause/bottleneck the evidence cannot establish.
OVERCLAIM = re.compile(
    r"\b(clearly|obviously|undoubtedly|definitely|the cause|caus(e|es|ed|ing)|root cause|"
    r"(the|a|main|key) bottleneck|bottleneck of|is the (constraint|problem)|"
    r"(is|are|be|we are|it is) (certain|sure|guaranteed))\b",
    re.IGNORECASE,
)
EVALUATIVE = re.compile(
    r"\b(safe|safer|safely|safety|better|worse|improv\w*|benefi\w*|effective\w*|efficacy|"
    r"success\w*|optimal|best|worst|harmful|worth it|should be (adopted|used))\b",
    re.IGNORECASE,
)
CLINICAL = re.compile(
    r"\b(dose|dosage|medication|prescrib\w*|administer\w*|diagnos\w*|treatment plan|"
    r"(the|this|a) patient (should|must|needs)|patients? (should|must) (be|receive|get)|"
    r"(assign|give|set)\w* (an? )?esi|esi (level )?(should|must)|resuscitat\w* (the|this))\b",
    re.IGNORECASE,
)
CI_MISREAD = [
    re.compile(
        r"\b(most|typical|usual|nearly all|almost all|the majority of)\s+(simulated\s+)?"
        r"(weeks?|days?)\b",
        re.I,
    ),
    re.compile(r"\b(confident|confidence) (that )?the (real|true|actual)", re.I),
    re.compile(r"\b(every|each|all)\s+(simulated\s+)?(weeks?|days?)\b", re.I),
    re.compile(r"95\s*(%|percent)\s*(chance|probability|likel\w*|certain\w*|sure)", re.I),
    re.compile(r"\b(probability|chance|likelihood)\b[^.]{0,50}\b(true|real|actual)\b", re.I),
    re.compile(r"\b(prove[sdn]?|proof|guarantee\w*|definitive\w*|conclusive\w*|certainly)\b", re.I),
    re.compile(
        r"\b(interval|CI)\b[^.]{0,80}\b(each|every|any|a single|individual)\s+"
        r"(simulated\s+)?(week|patient|day)",
        re.I,
    ),
    re.compile(
        r"\b(each|every|any|a single|individual)\s+(simulated\s+)?(week|patient|day)s?\b"
        r"[^.]{0,80}\b(interval|CI)\b",
        re.I,
    ),
]
PREDICTION = re.compile(
    r"\b(will|would|can|could)\s+(reduce|lower|cut|shorten|decrease|improve|increase|raise|"
    r"lengthen|fix|solve|eliminate|save)\b",
    re.IGNORECASE,
)
SOURCES = re.compile(
    r"https?://|www\.|\b\w+\.(com|org|gov|edu|net|io)\b|\bdoi\b|et al\.|\[\d+\]|"
    r"according to (a |the )?(study|studies|research|literature|cms|cdc|guideline\w*|report)|"
    r"\((19|20)\d{2}\)|\b(journal|published (study|research|paper))\b|\bsources?:|"
    r"\b(colleagues|coauthors|researchers|studies show|research shows|evidence shows|visit|"
    r"website|our site|click)\b",
    re.IGNORECASE,
)
# Outside authorities: allowed only in a negated sentence ("not clinical guidance").
SOURCE_WORDS = re.compile(
    r"\b(literature|guidance|guidelines?|best practices?|cms|cdc|ahrq|acep|ihi|nhs|"
    r"joint commission|hospital compare)\b",
    re.IGNORECASE,
)
MARKUP = re.compile(r"<[a-zA-Z/!][^>]*>|\[[^\]]*\]\([^)]*\)|```")
LEAK = re.compile(
    r"system prompt|ignore (all |the )?(previous|prior)|<\s*/?\s*evidence_packet|as an ai\b",
    re.IGNORECASE,
)
NEGATION = re.compile(
    r"\b(not|no|never|cannot|can't|isn't|aren't|doesn't|don't|didn't|without|nor|neither|"
    r"none|nothing|unable)\b",
    re.IGNORECASE,
)
MODEL_CONTEXT = re.compile(r"\b(model|simulat\w*|fictional)\b", re.IGNORECASE)
LOWER_WORDS = re.compile(r"\b(lower|reduc\w*|decreas\w*|shorter|fell|fewer|dropp?\w*)\b", re.I)
HIGHER_WORDS = re.compile(r"\b(higher|increas\w*|longer|rose|grew|ris\w*)\b", re.I)
HEDGE = re.compile(
    r"no detectable|not detectable|no clear|inconclusive|includes (0|zero)|crosses (0|zero)|"
    r"uncertain|cannot tell|not distinguishable|indistinguishable|identical|no change|"
    r"unchanged|no measurable|not measurably",
    re.IGNORECASE,
)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?;])\s+", text) if s.strip()]


def _negated(sentence: str, start: int, window: int = 12) -> bool:
    """True when a negation word appears within `window` words before position `start`."""
    before = sentence[:start].split()[-window:]
    return bool(NEGATION.search(" ".join(before)))


def _unnegated(pattern: re.Pattern[str], text: str) -> list[str]:
    hits = []
    for s in _sentences(text):
        for m in pattern.finditer(s):
            if not _negated(s, m.start()):
                hits.append(m.group(0))
    return hits


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def parse(raw: str) -> tuple[Interpretation | None, list[str]]:
    if len(raw) > MAX_RESPONSE_CHARS:
        return None, [f"response is {len(raw)} characters (limit {MAX_RESPONSE_CHARS})"]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, [f"not valid JSON ({exc.msg})"]
    try:
        return Interpretation.model_validate(data), []
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(p) for p in e['loc']) or 'response'}: {e['msg']}"
            for e in exc.errors()[:8]
        ]


def verify(packet: EvidencePacket, raw: str) -> VerificationReport:
    """Run every check. The interpretation is returned only through a passing report."""
    interp, schema_problems = parse(raw)
    schema = CheckOutcome(
        "structure", "Valid JSON with every required field and size limit", schema_problems
    )
    if interp is None:
        return VerificationReport([schema])

    ids = packet.ids
    cites = CheckOutcome("evidence_ids", "Every cited evidence ID exists in the packet")
    tokens = CheckOutcome("value_tokens", "Value tokens name a cited fact and a known field")
    numbers = CheckOutcome("numbers_and_units", "Typed numbers match packet values and units")
    direction = CheckOutcome("direction", "Direction words agree with the packet")
    ci = CheckOutcome(
        "uncertainty_wording",
        "No misreading of the 95% CI, forecasts, overclaimed certainty, or causes",
    )
    real = CheckOutcome("real_world_claims", "No real hospitals, places, or real-world causes")
    advice = CheckOutcome(
        "recommendations_and_safety",
        "No recommendations, safety/efficacy claims, unmeasured outcomes, or clinical directives",
    )
    sources = CheckOutcome("sources_and_markup", "No URLs, invented citations, markup, or leaks")
    required = CheckOutcome("required_content", "Cautions, limitations, and disclaimer present")

    all_facts = list(packet.facts)
    global_units, global_bare = _approved(all_facts)

    # --- citations, tokens, numbers, and direction per cited statement ---------------
    for section, st in interp.statements():
        unknown = [i for i in st.evidence_ids if i not in ids]
        if unknown:
            cites.problems.append(f"{section}: unknown evidence IDs {unknown}")
        cited = [packet.fact(i) for i in st.evidence_ids if i in ids]
        _check_tokens(st.text, set(st.evidence_ids), packet, tokens, section)
        units, bare = _approved([f for f in cited if f is not None])
        bare |= {95.0, 90.0, 50.0}
        _check_numbers(st.text, units, bare, numbers, section)
        _check_direction(
            st.text, [f for f in cited if isinstance(f, MetricFact)], direction, section
        )

    for section, text in [("summary", interp.summary), ("disclaimer", interp.disclaimer)] + [
        ("questions_for_humans", q) for q in interp.questions_for_humans
    ]:
        _check_tokens(text, ids, packet, tokens, section)
        _check_numbers(text, global_units, global_bare | {95.0, 90.0, 50.0}, numbers, section)

    # --- language checks over every piece of prose -----------------------------------
    for section, text in interp.free_texts():
        plain = TOKEN.sub(" value ", text)
        if m := _SPELLED.search(plain):
            numbers.problems.append(f"{section}: spelled-out quantity '{m.group(0)}'")
        if m := _RELATIVE.search(plain):
            numbers.problems.append(f"{section}: derived/relative quantity '{m.group(0)}'")
        for pat in CI_MISREAD:
            for hit in _unnegated(pat, plain):
                ci.problems.append(f"{section}: '{hit}'")
        for s in _sentences(plain):
            for m in PREDICTION.finditer(s):
                if not MODEL_CONTEXT.search(s) and not _negated(s, m.start()):
                    ci.problems.append(f"{section}: forecast without model context '{m.group(0)}'")
        if section != "questions_for_humans":  # questions may ask what would happen
            for hit in _unnegated(FUTURE, plain):
                ci.problems.append(f"{section}: future/implementation claim '{hit}'")
        for hit in _unnegated(OVERCLAIM, plain):
            ci.problems.append(f"{section}: overclaims certainty or a cause '{hit}'")
        if m := REAL_HOSPITALS.search(plain):
            real.problems.append(f"{section}: names a real hospital ('{m.group(0)}')")
        for s in _sentences(plain):
            rw = REAL_WORLD.search(s)
            if rw and section != "questions_for_humans" and not NEGATION.search(s):
                real.problems.append(f"{section}: real-world claim '{rw.group(0)}'")
            if rw and (cm := CAUSAL.search(s)) and not _negated(s, cm.start(), window=12):
                real.problems.append(f"{section}: real-world cause '{cm.group(0)}'")
        language = [(RECOMMEND, "recommendation"), (CLINICAL, "clinical directive")]
        if section != "questions_for_humans":  # a question may ask humans to judge safety
            language += [(EVALUATIVE, "evaluative/safety"), (UNMEASURED, "unmeasured outcome")]
        for pat, label in language:
            for hit in _unnegated(pat, plain):
                advice.problems.append(f"{section}: {label} wording '{hit}'")
        if m := SOURCES.search(text):
            sources.problems.append(f"{section}: source or URL '{m.group(0)}'")
        for hit in _unnegated(SOURCE_WORDS, plain):
            sources.problems.append(f"{section}: outside source '{hit}'")
        if m := MARKUP.search(text):
            sources.problems.append(f"{section}: markup '{m.group(0)[:30]}'")
        if m := LEAK.search(text):
            sources.problems.append(f"{section}: prompt text or injection echo '{m.group(0)}'")

    # --- required content -------------------------------------------------------------
    focus = f"M:{packet.focus_metric}"
    if not any(focus in s.evidence_ids for s in interp.key_findings):
        required.problems.append(f"no key finding cites the focus outcome {focus}")
    for s in interp.key_findings:
        if not any(i.startswith("M:") for i in s.evidence_ids):
            required.problems.append("a key finding cites no measured outcome (M:)")
    if not any(i.startswith("LIM:") for s in interp.limitations for i in s.evidence_ids):
        required.problems.append("limitations cite no LIM: fact")
    if packet.validation_status == "CAUTION":
        cited_caution = any(
            i.startswith("CK:") or i == "ST:status"
            for _, s in interp.statements()
            for i in s.evidence_ids
        )
        said = any("caution" in t.lower() for _, t in interp.free_texts())
        if not (cited_caution and said):
            required.problems.append("CAUTION status is not stated and cited (CK: or ST:status)")
    if not MODEL_CONTEXT.search(interp.summary):
        required.problems.append("summary does not say the results are from the fictional model")
    d = interp.disclaimer.lower()
    if not (("fictional" in d or "simulat" in d) and ("human" in d or "recommendation" in d)):
        required.problems.append(
            "disclaimer must mention the fictional simulation and human review / no recommendation"
        )
    if not all(q.strip().endswith("?") for q in interp.questions_for_humans):
        required.problems.append("questions for humans must be questions")

    checks = [schema, cites, tokens, numbers, direction, ci, real, advice, sources, required]
    return VerificationReport(checks, interp)


def _check_tokens(
    text: str, allowed: set[str], packet: EvidencePacket, out: CheckOutcome, section: str
) -> None:
    for m in TOKEN.finditer(text):
        fid, fld = m.group(1), m.group(2)
        if fld not in TOKEN_FIELDS:
            out.problems.append(f"{section}: unknown token field '{fld}'")
        elif not isinstance(packet.fact(fid), MetricFact):
            out.problems.append(f"{section}: token uses unknown or non-numeric fact '{fid}'")
        elif fid not in allowed:
            out.problems.append(f"{section}: token uses {fid}, which this statement does not cite")
    leftover = TOKEN.sub("", text)
    if "{{" in leftover or "}}" in leftover:
        out.problems.append(f"{section}: malformed value token")


def _check_numbers(
    text: str,
    units: set[tuple[float, str]],
    bare: set[float],
    out: CheckOutcome,
    section: str,
) -> None:
    for x, cls, _ in extract_numbers(text):
        if cls is None:
            if not any(_matches(x, v) for v in bare):
                out.problems.append(f"{section}: number {x:g} is not in the evidence")
        elif not any(_matches(x, v) and c == cls for v, c in units):
            same_value = any(_matches(x, v) for v, _ in units)
            why = "wrong unit" if same_value else "not in the cited evidence"
            out.problems.append(f"{section}: '{x:g} {cls}' ({why})")


def _check_direction(
    text: str, metric_facts: list[MetricFact], out: CheckOutcome, section: str
) -> None:
    if not metric_facts:
        return
    plain = TOKEN.sub(" value ", text)
    signed = [f for f in metric_facts if f.direction.startswith(("lower", "higher"))]
    unsigned = [f for f in metric_facts if f not in signed]
    hedged = bool(HEDGE.search(text)) or any(
        f"{{{{{f.id}.change}}}}" in text.replace(" ", "") for f in unsigned
    )
    says_lower, says_higher = bool(LOWER_WORDS.search(plain)), bool(HIGHER_WORDS.search(plain))
    if unsigned and not signed and (says_lower or says_higher) and not hedged:
        out.problems.append(
            f"{section}: describes a change for {[f.id for f in unsigned]}, whose 95% CI includes 0"
        )
    if len(signed) == 1 and not unsigned:
        f = signed[0]
        if f.direction.startswith("lower") and says_higher and not says_lower:
            out.problems.append(f"{section}: says higher, but {f.id} is lower in the model")
        if f.direction.startswith("higher") and says_lower and not says_higher:
            out.problems.append(f"{section}: says lower, but {f.id} is higher in the model")
    # A typed difference must be followed by the right direction word.
    for x, _, end in extract_numbers(text):
        tail = " ".join(normalize(text)[end:].split()[:6])
        for f in signed:
            if _matches(x, f.difference) and abs(f.difference) >= 0.1:
                word_lower = LOWER_WORDS.search(tail)
                word_higher = HIGHER_WORDS.search(tail)
                if f.direction.startswith("lower") and word_higher and not word_lower:
                    out.problems.append(f"{section}: {x:g} described as higher; {f.id} is lower")
                if f.direction.startswith("higher") and word_lower and not word_higher:
                    out.problems.append(f"{section}: {x:g} described as lower; {f.id} is higher")
