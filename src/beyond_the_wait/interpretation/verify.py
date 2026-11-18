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


def headline(packet: EvidencePacket) -> str:
    """The key result as a Python-rendered sentence (no generated text involved).

    The dashboard shows this above any explanation, so the most important
    sentence never depends on Claude's wording.
    """
    focus = packet.fact(f"M:{packet.focus_metric}")
    assert isinstance(focus, MetricFact)
    return (
        f"In the fictional model, '{packet.scenario_name}' compared with the baseline: "
        f"{focus.label} was {render_value(focus, 'change')} "
        f"(baseline {render_value(focus, 'baseline')}, with the change "
        f"{render_value(focus, 'intervention')}; {focus.n_pairs} paired simulated weeks). "
        f"Validation status: {packet.validation_status}."
    )


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
_CI_PHRASE = re.compile(
    r"95\s*(%|percent)\s*(CI\b|confidence|intervals?\b|uncertainty intervals?\b)", re.IGNORECASE
)
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


_ESI = re.compile(r"\bESI[- ]?[1-5]\b", re.I)


def normalize(text: str) -> str:
    """Text with value tokens, fact IDs and the '95% CI' phrase removed (not numbers)."""
    text = _ESI.sub(" ", TOKEN.sub(" ", text))
    return _CI_PHRASE.sub("CI", FACT_ID.sub(" ", text))


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
            if f.metric.endswith("_p90"):  # "90% of patients" / "9 in 10" readings
                with_unit.add((90.0, "pct"))
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
    r"most (eds|hospitals)|typical (eds|hospitals)|elsewhere|generali[sz]\w*|"
    r"emergency departments|hospitals|eds|health systems?|today|nowadays|these days|"
    r"real life|mirrors? (what|how)|hospital labs)",
    re.IGNORECASE,
)
CAUSAL = re.compile(
    r"\b(because|due to|caused by|causes?|driven by|explains? why|the reason|root cause)\b",
    re.IGNORECASE,
)
RECOMMEND = re.compile(
    r"\b(recommend\w*|advis\w*|urge[sd]?|urging|we suggest|i suggest|you should|"
    r"consider (hiring|adding|"
    r"staffing|implementing|moving)|(should|must|ought to|needs? to)\s+([\w-]+\s+){0,3}?"
    r"(hire|add|staff|implement|adopt|deploy|invest|reduce|increase|cut|move|reassign|schedule|prioriti[sz]e|discharge|"
    r"admit|treat|triage|expand|fund|purchase|open|close|roll out|pilot))\b|"
    r"\b(would be (wise|prudent|sensible|advisable)|(is|are|was) (the )?(right|smart|wise) "
    r"(call|choice|move|decision)|good (investment|idea|choice|option|bet)|go ahead|"
    r"worth (it|doing|trying|considering|pursuing)|no downside|low[- ]risk|risk[- ]free|harmless|"
    r"makes? sense to|move forward with|opt for|go with|the way to go|prioriti[sz]e|"
    r"(it is|it's) (advisable|prudent|sensible)|justif\w*|(strong |good )?case for|encourag\w*|"
    r"proven|what (this|the) ed (lacks|needs)|lacks|understaffed|needs more|"
    r"reassur\w*|promising|compelling|persuasive|convincing|attractive|favou?rable|"
    r"good news|bodes well|positive (sign|signal|news|result)|deserv\w*|merits?|"
    r"warrants? (action|investment|adoption|implementation|attention|funding)|"
    r"calls? for (action|investment|adding|hiring|more)|"
    r"(serious|careful|close) (attention|consideration) from|"
    r"should be (considered|explored|pursued|prioriti[sz]ed|adopted)|"
    r"(is|are) supported by (the|this) (model|simulation|evidence|results?|analysis)|"
    r"(the|this) (model|simulation|evidence|analysis) (supports|backs|favou?rs|argues for)|"
    r"in favou?r of|argues? for|argument (for|in favou?r)|move ahead|go forward with|"
    r"proceed with|act on|enough to act|actionable|"
    r"(sensible|reasonable|logical|natural|obvious|smart|prudent|wise) (next )?"
    r"(step|choice|option|move|approach)|next steps?|"
    r"for (leaders|leadership|managers|administrators|decision[- ]makers|the board) to "
    r"(explore|consider|pursue|weigh|adopt|try)|worth (exploring|a try|a look|pursuing)|"
    r"(hard|difficult) to (argue|object) (against|with)|argue against|"
    r"no (good )?reason (not )?to|why not (add|hire|try)|(a )?mistake not to|"
    r"(only|clear|obvious) (choice|answer|option)|approv(e|es|al of)|"
    r"should be (added|hired|implemented|funded|expanded|staffed|deployed|introduced|done))\b|"
    # Imperatives at the start of a sentence ("Hire a nurse.").
    r"(^|[.;:!?]\s+)(hire|add|staff|implement|adopt|deploy|invest|fund|purchase|expand|pilot|"
    r"reassign|prioriti[sz]e|schedule|cut|reduce|increase|open|close|approve|start|stop)\s+"
    r"(a|an|the|one|more|another|extra|additional|this|that|these|those|it)\b",
    re.IGNORECASE,
)
# Outcomes the model never measures: claiming them is unsupported.
UNMEASURED = re.compile(
    r"\b(mortality|deaths?|die|dying|lives|harm\w*|morbidity|adverse events?|patient outcomes?|"
    r"clinical outcomes?|quality of care|satisfaction|errors?|complications?|sicker|"
    r"care sooner|faster care|timely care|treated sooner|leaves? without being seen|"
    r"left without being seen|lwbs|walk[- ]?outs?|faster (treatment|diagnosis|attention)|"
    r"(treated|diagnosed|stabili[sz]ed) (faster|earlier|more quickly|sooner)|"
    r"(received|got|get|gets) (care|treatment) (sooner|faster|earlier)|"
    r"(earlier|quicker|prompter) (care|treatment)|time to treatment)\b",
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
    r"(the|a|main|key) bottleneck|bottleneck of|is the (constraint|problem)|what limits|"
    r"limiting (factor|step|resource|constraint)|rate[- ]limiting|binding constraint|"
    r"(the|main|key|real|true) constraint|holds? (this ed|the ed|flow|patients) back|"
    r"(is|are|be|we are|it is) (certain|sure|guaranteed)|no doubt|beyond doubt|"
    r"without (a )?doubt|unquestionabl\w*)\b",
    re.IGNORECASE,
)
EVALUATIVE = re.compile(
    r"\b(safe|safer|safely|safety|better|worse|improv\w*|benefi\w*|effective\w*|efficacy|"
    r"success\w*|optimal|best|worst|harmful|worth it|should be (adopted|used)|"
    r"helps? (patients|people|staff|the ed)|helped (patients|people|staff)|"
    r"good for (patients|staff|the ed)|patients (gain|win|do better)|serves? patients)\b",
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
        r"\b(future|next|upcoming|coming|later|subsequent)\s+(simulated\s+)?(weeks?|days?|runs?|"
        r"months?|periods?)\b",
        re.I,
    ),
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
    r"\b(colleagues|coauthors|researchers|studies show|research shows|evidence shows|"
    r"visit (our|the|this|my) (site|website|page)|"
    r"website|our site|click)\b",
    re.IGNORECASE,
)
# Outside authorities: allowed only in a negated sentence ("not clinical guidance").
SOURCE_WORDS = re.compile(
    r"\b(literature|guidance|guidelines?|best practices?|cms|cdc|ahrq|acep|ihi|nhs|"
    r"joint commission|hospital compare)\b",
    re.IGNORECASE,
)
# "No risk" / "no safety concern" ASSERTS safety, so negation does not exempt it.
SAFETY_ASSERT = re.compile(
    r"\b(no|zero|without( any)?|free of|minimal|negligible|little|low) (added |additional |"
    r"extra |new |real |meaningful )?(patient[- ])?(safety[- ])?(risks?|harms?|dangers?|hazards?|"
    r"concerns?|issues?)\b|\bposes? no\b|\b(risk|harm)[- ]free\b",
    re.IGNORECASE,
)
MARKUP = re.compile(r"<[a-zA-Z/!][^>]*>|\[[^\]]*\]\([^)]*\)|```")
LEAK = re.compile(
    r"system prompt|ignore (all |the )?(previous|prior)|<\s*/?\s*evidence_packet|as an ai\b|"
    r"\b(system|developer|assistant|user|human)\s*:|as (instructed|requested|directed)|"
    r"following the (note|instructions?|request)|per the (note|instructions?)|"
    r"the note (attached|says|asks)|(cautions?|status|limitations?|warnings?) "
    r"(were|was|are|is|can be|may be) (waived|ignored|dropped|lifted)",
    re.IGNORECASE,
)
NEGATION = re.compile(
    r"\b(not|no|never|cannot|can't|isn't|aren't|doesn't|don't|didn't|without|nor|neither|"
    r"none|nothing|unable)\b",
    re.IGNORECASE,
)
MODEL_CONTEXT = re.compile(r"\b(model|simulat\w*|fictional)\b", re.IGNORECASE)
LOWER_WORDS = re.compile(r"\b(lower|reduc\w*|decreas\w*|shorter|fell|fewer|dropp?\w*)\b", re.I)
HIGHER_WORDS = re.compile(r"\b(higher|increas\w*|longer|rose|grew|ris(e|es|en|ing))\b", re.I)
HEDGE = re.compile(
    r"no detectable|not detectable|no clear|inconclusive|includes (0|zero)|crosses (0|zero)|"
    r"uncertain|cannot tell|not distinguishable|indistinguishable|identical|no change|"
    r"unchanged|no measurable|not measurably|cannot (say|determine|rule|distinguish|separate)|"
    r"can'?t (say|tell|rule)|either direction|in or out|neither\b.{0,40}\bnor\b|"
    r"can(not|'t)? be read|cannot be read",
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
# Claim alignment (Milestone 9): does the wording match the facts it cites?
# ---------------------------------------------------------------------------

# Outcome families: words a reader uses to name an outcome. A value token is
# mislabeled when the words in front of it name a different family.
FAMILY_WORDS: dict[str, re.Pattern[str]] = {
    "triage": re.compile(r"\btriage\b", re.I),
    "physician": re.compile(r"\b(physicians?|providers?|doctors?|clinicians?)\b", re.I),
    "los": re.compile(r"\b(length of stay|lengths of stay|los|stays?|time in the ed)\b", re.I),
    "boarding": re.compile(r"\b(board\w*|inpatient beds?)\b", re.I),
    "utilization": re.compile(r"\b(utili[sz]ation|busy|busyness|workload|occupancy)\b", re.I),
    "space": re.compile(r"\b(treatment spaces?|spaces?)\b", re.I),
    "results": re.compile(
        r"\b(labs?|laboratory|imaging|diagnostics?|test results|result waits?|"
        r"waits? for (lab |imaging )?results|results stage)\b|\bresults?[\s:(]*$",
        re.I,
    ),
}
METRIC_FAMILIES: dict[str, set[str]] = {
    "arrival_to_provider_median": {"physician"},
    "arrival_to_provider_p90": {"physician"},
    "triage_wait_p90": {"triage"},
    "provider_wait_p90": {"physician"},
    "los_median_discharged": {"los"},
    "los_median_admitted": {"los"},
    "los_median_lower_bound_all": {"los"},
    "boarding_p90": {"boarding"},
    "boarding_share_over_4h": {"boarding"},
    "physician_utilization": {"utilization", "physician"},
    "triage_utilization": {"utilization", "triage"},
    "stage_triage_wait_mean": {"triage"},
    "stage_space_wait_mean": {"space"},
    "stage_provider_wait_mean": {"physician"},
    "stage_results_mean": {"results"},
    "stage_boarding_mean": {"boarding"},
}
STAT_WORDS: dict[str, re.Pattern[str]] = {
    "max": re.compile(r"\b(longest|maximum|max|worst[- ]case|peak|highest)\b", re.I),
    "median": re.compile(r"\b(median|typical|the middle patient)\b", re.I),
    "p90": re.compile(
        r"\b(90th|p90|ninetieth|9 in 10|nine in ten|90 percent of patients|longest waits?)\b", re.I
    ),
    "mean": re.compile(
        r"(?<!\bon )\b(mean|average)\b(?! (change|difference|across|over|effect))", re.I
    ),
    "share": re.compile(r"\b(share|proportion|fraction|percentage) of\b", re.I),
}
# A phrase naming the intervention ("an extra night triage nurse") names
# staff, not an outcome, so it is removed before outcome words are matched.
_STAFF = r"(triage\s+nurses?|physicians?|doctors?|providers?|nurses?)"
_ADDED = r"(add(ed|ing|s)?|extra|additional|another|second|new|one more)"
_STAFFING = re.compile(
    rf"\b({_ADDED}\s+)?(night|evening|overnight|day)\s+{_STAFF}\b|\b{_ADDED}\s+{_STAFF}\b|"
    r"\bbed releases?\b|\bfaster diagnostics\b",
    re.I,
)


def metric_stat(metric: str) -> str | None:
    if metric.endswith("_median") or "_median_" in metric:
        return "median"
    if metric.endswith("_p90"):
        return "p90"
    if metric.endswith("_mean"):
        return "mean"
    if metric.startswith("boarding_share"):
        return "share"
    return None


def _without_names(text: str, packet: EvidencePacket) -> str:
    """Prose with the scenario name and change description (packet data) removed."""
    for name in (packet.scenario_name, *packet.what_changes):
        if name:
            text = re.sub(re.escape(name), " ", text, flags=re.I)
    return text


def _outcome_text(text: str, packet: EvidencePacket) -> str:
    """Prose with the scenario name and staffing phrases removed (they are not outcomes)."""
    return _STAFFING.sub(" ", _without_names(text, packet))


def families_in(text: str) -> set[str]:
    return {fam for fam, pat in FAMILY_WORDS.items() if pat.search(text)}


def stats_in(text: str) -> set[str]:
    return {s for s, pat in STAT_WORDS.items() if pat.search(text)}


def token_segments(text: str) -> list[tuple[str, str, str]]:
    """(fact_id, field, prose since the previous token) for each value token."""
    out, last = [], 0
    for m in TOKEN.finditer(text):
        out.append((m.group(1), m.group(2), text[last : m.start()]))
        last = m.end()
    return out


# Sensitivity verdicts written by the packet builder (see evidence.VERDICTS).
SENS_UNSTABLE = re.compile(r"depends on the uncertain|reverses direction|shows no detectable", re.I)
SENS_STABLE = re.compile(r"in every setting", re.I)
ROBUST_WORDS = re.compile(
    r"\b(robust\w*|stable|stability holds|holds up|held up|consistent(ly)? across|"
    r"(holds|held|persists?|persisted) (across|in|under|for) (all|every|each|both|the)|"
    r"regardless of|in every setting|in all settings|confirm\w*|insensitive)\b",
    re.I,
)
FRAGILE_WORDS = re.compile(
    r"\b(unstable|fragile|depends on|dependent on|revers\w*|not robust|sensitive to|"
    r"inconsistent)\b",
    re.I,
)
VALIDATION_OVERCLAIM = re.compile(
    r"\b((clinically|fully|externally|independently|formally) (validated|verified|proven|tested)|"
    r"validated (model|results?|findings?|estimates?|numbers?|effect)|"
    r"validat(es|ing) (the|this|a|our|that)|"
    r"(results?|findings?|numbers?|model|estimates?|this) (is|are|was|were|has been|have been) "
    r"validated|passed (all|every)( \w+)? (validation|checks?|tests?)|"
    r"(status|comparison) (is|was) pass\b|no (cautions?|warnings?|caveats?|concerns?)\b)",
    re.I,
)
# Treating an inconclusive interval (or a measured change) as proof of no effect.
ABSENCE = re.compile(
    r"\b(no effect|unaffected|not affected|had no (impact|effect)|no impact|made no difference|"
    r"(did|does|do) not (change|move|affect)(?! detectabl| measurabl)|didn'?t (change|move|affect)"
    r"(?! detectabl| measurabl)|stayed (the same|flat|put)|remained (the same|flat|steady)|"
    r"held steady|no change|unchanged|flat|ruled out|"
    r"not (influenced|affected|impacted|altered|moved|changed) by|"
    r"(insensitive|immune|unresponsive|unrelated) to|has no (bearing|influence|relationship)|"
    r"(zero|null|nil) (effect|impact|change|difference))\b",
    re.I,
)
QUALIFIER_WORDS = {
    "discharged": re.compile(r"\b(discharg\w*|went home|sent home|go(es)? home)\b", re.I),
    "admitted": re.compile(r"\b(admitted|admissions?|hospitali[sz]ed)\b", re.I),
    "all": re.compile(r"\b(all patients|every patient|overall)\b", re.I),
}
METRIC_QUALIFIER = {
    "los_median_discharged": "discharged",
    "los_median_admitted": "admitted",
    "los_median_lower_bound_all": "all",
}
WAIT_KIND = re.compile(r"\b(waits?|waiting|waited|time|times|stays?|delays?|boarding)\b", re.I)
UTIL_KIND = re.compile(r"\b(utili[sz]ation|busy|busyness|workload|occupancy|in use)\b", re.I)
RELIABILITY = re.compile(
    r"\b((is|are|be|remains?|looks?|seems?) (fully |highly |very |quite |entirely )?"
    r"(dependable|reliable|trustworthy|credible|solid|sound|definitive|precise|settled|"
    r"conclusive)|(precise|settled) and|"
    r"the only (real |true |important |meaningful )?(limitation|caveat|caution|concern)|"
    r"no (other |further |additional |real |major |special )?(limitations?|caveats?|cautions?) "
    r"(apply|applies|remain|matter|exist)|"
    r"otherwise (the )?(pattern|result|findings?|model|numbers?) (is|are) \w+)\b",
    re.I,
)
# Capacity or stability claims; the packet's overload cautions contradict them.
CAPACITY_CLAIM = re.compile(
    r"\b((enough|sufficient|adequate|ample|spare|extra) capacity|"
    r"(handle|absorb|cope with|keep up with|kept up with) (all|every|the) "
    r"(arrivals|patients|demand)|"
    r"(no|without) (overload\w*|crowding)|queues? (stayed|remained|were|was) (under control|stable|"
    r"short)|under control)\b",
    re.I,
)
# Phrases that explicitly deny the absence claim that follows them.
ABSENCE_GUARD = re.compile(
    r"\b(not the same as|(does|do|did) not (mean|show|prove|establish|imply)|"
    r"(doesn'?t|don'?t|didn'?t) (mean|show|prove|imply)|(is|are) not (evidence|proof)|"
    r"(isn'?t|aren'?t) (evidence|proof)|cannot (show|tell|establish|conclude|prove)|"
    r"can'?t (show|tell|prove)|not (evidence|proof) of|rather than|instead of|"
    r"no basis for|would be wrong to|not evidence (of|that)|absence of evidence|"
    r"not (a )?proof (of|that))\b",
    re.I,
)
SWEEPING = re.compile(
    r"\b(across the board|across all (outcomes|measures|metrics|stages|waits)|"
    r"all (the )?(waits|wait times|outcomes|measures|metrics|stages|delays)|"
    r"every (wait|outcome|measure|metric|stage|delay)|uniformly|universally|everywhere|"
    r"without exception|consistently (lower|shorter|higher|longer)|"
    r"(no|without( any)?|free of|zero) (trade-?offs?|downsides?|costs?|side[- ]effects?|"
    r"drawbacks?|penalt(y|ies))|nothing (got|was|became|went) (worse|higher|longer|up)|"
    r"(only|purely|pure) (positive|good|favou?rable|gains?|wins?|lower)|"
    r"nothing else\b.{0,40}\b(worse|wrong|higher|longer|up))\b",
    re.I,
)
OUTCOME_METRICS = {m for m, fams in METRIC_FAMILIES.items() if "utilization" not in fams}


def opposing_facts(packet: EvidencePacket) -> list[MetricFact]:
    """Outcome facts (not workload) that moved opposite to the focus outcome."""
    focus = packet.fact(f"M:{packet.focus_metric}")
    if not isinstance(focus, MetricFact) or not focus.direction.startswith(("lower", "higher")):
        return []
    want = "higher" if focus.direction.startswith("lower") else "lower"
    return [
        f
        for f in packet.metric_facts
        if f.metric in OUTCOME_METRICS and f.direction.startswith(want)
    ]


def _mixed_outcomes(packet: EvidencePacket) -> bool:
    dirs = {f.direction.split()[0] for f in packet.metric_facts if f.metric in OUTCOME_METRICS}
    return len(dirs) > 1


def _check_alignment(
    text: str,
    cited: list[MetricFact | TextFact],
    packet: EvidencePacket,
    out: CheckOutcome,
    loc: str,
) -> None:
    prose = _outcome_text(text, packet)
    whole_fams = families_in(TOKEN.sub(" ", prose))
    cited_metrics = [f for f in cited if isinstance(f, MetricFact)]
    for fid, fld, before in token_segments(prose):
        fact = packet.fact(fid)
        if not isinstance(fact, MetricFact) or fld not in TOKEN_FIELDS:
            continue  # reported by the value_tokens check
        own = METRIC_FAMILIES.get(fact.metric, set())
        near = families_in(before)
        named = near or whole_fams
        if named and not (named & own) and not (len(cited_metrics) > 1 and not near):
            out.problems.append(
                f"{loc}: value of {fid} is presented as {sorted(named)[0]}, not "
                f"{'/'.join(sorted(own))}"
            )
        near_text = before if near else prose
        if "utilization" in own and WAIT_KIND.search(near_text) and not UTIL_KIND.search(near_text):
            out.problems.append(f"{loc}: workload value {fid} is described as a wait or time")
        if "utilization" not in own and UTIL_KIND.search(near_text):
            if not WAIT_KIND.search(near_text):
                out.problems.append(f"{loc}: wait/time value {fid} is described as workload")
        qual = METRIC_QUALIFIER.get(fact.metric)
        if qual:
            named_q = {q for q, pat in QUALIFIER_WORDS.items() if pat.search(near_text)}
            if named_q and qual not in named_q:
                out.problems.append(
                    f"{loc}: value of {fid} ({qual} patients) is described as "
                    f"{sorted(named_q)[0]} patients"
                )
        stat, said = metric_stat(fact.metric), stats_in(before)
        if stat and said and stat not in said:
            out.problems.append(f"{loc}: value of {fid} ({stat}) is described as {sorted(said)[0]}")
    sens = [f for f in cited if isinstance(f, TextFact) and f.kind == "sensitivity"]
    stated = {f.metric for f in cited_metrics} | {
        fact.metric
        for fid, _, _ in token_segments(text)
        if isinstance(fact := packet.fact(fid), MetricFact)
    }
    for f in sens:  # a sensitivity verdict supports only its own outcome
        if stated and f.id.split(":", 1)[1] not in stated:
            out.problems.append(
                f"{loc}: cites {f.id} for a statement about {sorted(stated)[0]}, a different "
                "outcome"
            )
    if sens and not stated:
        sens_fams = set().union(*(METRIC_FAMILIES.get(f.id.split(":", 1)[1], set()) for f in sens))
        named = whole_fams - {"utilization"}
        if named and not (named & sens_fams):
            out.problems.append(
                f"{loc}: cites {[f.id for f in sens]} to support a statement about "
                f"{sorted(named)[0]}"
            )
    for f in sens:
        if SENS_UNSTABLE.search(f.text) and _unnegated(ROBUST_WORDS, text):
            out.problems.append(f"{loc}: calls the result robust, but {f.id} says it is not")
        if SENS_STABLE.search(f.text) and _unnegated(FRAGILE_WORDS, text):
            out.problems.append(f"{loc}: calls the result fragile, but {f.id} says it is stable")
    for m in VALIDATION_OVERCLAIM.finditer(text):
        hit = m.group(0)
        if hit.lower().startswith("no "):  # "no cautions" is false only for a CAUTION packet
            if packet.validation_status == "CAUTION":
                out.problems.append(f"{loc}: says '{hit}', but the status is CAUTION")
        elif not _negated(text, m.start(), window=6):
            out.problems.append(f"{loc}: overstates validation ('{hit}')")
    for hit in _unnegated(RELIABILITY, text):
        out.problems.append(f"{loc}: overstates reliability or dismisses limitations ('{hit}')")
    cautions = {f.id for f in packet.facts if f.id.startswith("CK:")}
    if cautions & {"CK:overload_regime", "CK:week_capacity_and_stability"}:
        for hit in _unnegated(CAPACITY_CLAIM, text):
            out.problems.append(
                f"{loc}: capacity claim '{hit}' contradicts the packet's overload cautions"
            )
    for hit in _unnegated(SWEEPING, text):
        if _mixed_outcomes(packet):
            out.problems.append(
                f"{loc}: sweeping claim '{hit}', but outcomes moved in different directions"
            )


def _check_absence(
    text: str, cited: list[MetricFact | TextFact], out: CheckOutcome, loc: str
) -> None:
    metrics = [f for f in cited if isinstance(f, MetricFact)]
    if not metrics or all(f.direction.startswith("identical") for f in metrics):
        return
    plain = TOKEN.sub(" value ", text)
    for s in _sentences(plain):
        for m in ABSENCE.finditer(s):
            before = s[: m.start()].split()
            if ABSENCE_GUARD.search(" ".join(before[-10:])):
                continue  # "...is not the same as showing no effect"
            out.problems.append(
                f"{loc}: '{m.group(0)}' treats an interval that is not identical as proof of no "
                "effect (absence of evidence is not evidence of absence)"
            )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

CHECKS: tuple[tuple[str, str], ...] = (
    ("structure", "Valid JSON with every required field and size limit"),
    ("evidence_ids", "Every cited evidence ID exists in the packet"),
    ("value_tokens", "Value tokens name a cited fact and a known field"),
    ("numbers_and_units", "Typed numbers match packet values and units"),
    ("direction", "Direction words agree with the packet"),
    (
        "uncertainty_wording",
        "No misreading of the 95% CI, forecasts, overclaimed certainty, or causes",
    ),
    ("real_world_claims", "No real hospitals, places, or real-world causes"),
    (
        "recommendations_and_safety",
        "No recommendations, safety/efficacy claims, unmeasured outcomes, or clinical directives",
    ),
    ("claim_alignment", "Wording matches the outcomes, statistics, and verdicts it cites"),
    ("sources_and_markup", "No URLs, invented citations, markup, or leaks"),
    ("required_content", "Cautions, limitations, and disclaimer present"),
)


@dataclass(frozen=True)
class Located:
    """One piece of prose and where it sits. evidence_ids is None for uncited fields."""

    loc: str
    section: str
    text: str
    evidence_ids: tuple[str, ...] | None


def located_texts(interp: Interpretation) -> list[Located]:
    out = [Located("summary", "summary", interp.summary, None)]
    for section in ("key_findings", "tradeoffs", "uncertainties", "limitations"):
        for i, s in enumerate(getattr(interp, section)):
            out.append(Located(f"{section}[{i}]", section, s.text, tuple(s.evidence_ids)))
    out += [
        Located(f"questions_for_humans[{i}]", "questions_for_humans", q, None)
        for i, q in enumerate(interp.questions_for_humans)
    ]
    out.append(Located("disclaimer", "disclaimer", interp.disclaimer, None))
    return out


def new_checks() -> dict[str, CheckOutcome]:
    return {name: CheckOutcome(name, desc) for name, desc in CHECKS}


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
    outs = new_checks()
    outs["structure"].problems.extend(schema_problems)
    if interp is None:
        return VerificationReport([outs["structure"]])
    approved = _approved(list(packet.facts))
    for item in located_texts(interp):
        check_text(packet, item, outs, approved)
    _check_required(packet, interp, outs["required_content"])
    return VerificationReport(list(outs.values()), interp)


def check_text(
    packet: EvidencePacket,
    item: Located,
    outs: dict[str, CheckOutcome],
    approved: tuple[set[tuple[float, str]], set[float]] | None = None,
) -> None:
    """Every per-text check for one piece of prose; problems are recorded under item.loc."""
    loc, section, text = item.loc, item.section, item.text
    ids = packet.ids
    if item.evidence_ids is not None:
        unknown = [i for i in item.evidence_ids if i not in ids]
        if unknown:
            outs["evidence_ids"].problems.append(f"{loc}: unknown evidence IDs {unknown}")
        cited = [f for i in item.evidence_ids if (f := packet.fact(i)) is not None]
        _check_tokens(text, set(item.evidence_ids), packet, outs["value_tokens"], loc)
        units, bare = _approved(cited)
    else:
        # Uncited prose may use any packet value; its direction words are checked
        # against the facts its tokens name.
        cited = [f for m in TOKEN.finditer(text) if (f := packet.fact(m.group(1))) is not None]
        _check_tokens(text, ids, packet, outs["value_tokens"], loc)
        units, bare = approved or _approved(list(packet.facts))
    _check_numbers(
        _without_names(text, packet),
        units,
        bare | {95.0, 90.0, 50.0},
        outs["numbers_and_units"],
        loc,
    )
    metric_cited = [f for f in cited if isinstance(f, MetricFact)]
    _check_direction(
        _outcome_text(text, packet),
        metric_cited,
        outs["direction"],
        loc,
        packet if section not in ("questions_for_humans", "disclaimer") else None,
        whole_statement=item.evidence_ids is not None,
    )
    _check_language(packet, loc, section, text, outs, (units, bare))
    _check_alignment(text, cited, packet, outs["claim_alignment"], loc)
    _check_absence(text, cited, outs["uncertainty_wording"], loc)


WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90, "hundred": 100,
}  # fmt: skip


def _spelled_ok(m: re.Match[str], approved: tuple[set[tuple[float, str]], set[float]]) -> bool:
    """'four hours' is fine when 4 hours is a packet value; 'twenty-odd minutes' is not."""
    if m.group(2):  # a word between the number and the unit: not a plain quantity
        return False
    value = float(WORD_NUMBERS[m.group(1).lower()])
    cls = _unit_class(m.group(3).split()[0] if "point" not in m.group(3) else "points")
    return any(_matches(value, v) and c == cls for v, c in approved[0])


def _check_language(
    packet: EvidencePacket,
    loc: str,
    section: str,
    text: str,
    outs: dict[str, CheckOutcome],
    approved: tuple[set[tuple[float, str]], set[float]] = (set(), set()),
) -> None:
    numbers, ci, real = (
        outs["numbers_and_units"],
        outs["uncertainty_wording"],
        outs["real_world_claims"],
    )
    advice, sources = outs["recommendations_and_safety"], outs["sources_and_markup"]
    plain = TOKEN.sub(" value ", text)
    for m in _SPELLED.finditer(plain):
        if not _spelled_ok(m, approved):
            numbers.problems.append(f"{loc}: spelled-out quantity '{m.group(0)}'")
    if m := _RELATIVE.search(plain):
        numbers.problems.append(f"{loc}: derived/relative quantity '{m.group(0)}'")
    for pat in CI_MISREAD:
        for hit in _unnegated(pat, plain):
            ci.problems.append(f"{loc}: '{hit}'")
    for s in _sentences(plain):
        for m in PREDICTION.finditer(s):
            if not MODEL_CONTEXT.search(s) and not _negated(s, m.start()):
                ci.problems.append(f"{loc}: forecast without model context '{m.group(0)}'")
    if section != "questions_for_humans":  # questions may ask what would happen
        for hit in _unnegated(FUTURE, plain):
            ci.problems.append(f"{loc}: future/implementation claim '{hit}'")
    for hit in _unnegated(OVERCLAIM, plain):
        ci.problems.append(f"{loc}: overclaims certainty or a cause '{hit}'")
    if m := REAL_HOSPITALS.search(plain):
        real.problems.append(f"{loc}: names a real hospital ('{m.group(0)}')")
    for s in _sentences(plain):
        rw = REAL_WORLD.search(s)
        if rw and section != "questions_for_humans" and not NEGATION.search(s):
            real.problems.append(f"{loc}: real-world claim '{rw.group(0)}'")
        if rw and (cm := CAUSAL.search(s)) and not _negated(s, cm.start(), window=12):
            real.problems.append(f"{loc}: real-world cause '{cm.group(0)}'")
    language = [(RECOMMEND, "recommendation"), (CLINICAL, "clinical directive")]
    if section != "questions_for_humans":  # a question may ask humans to judge safety
        language += [(EVALUATIVE, "evaluative/safety"), (UNMEASURED, "unmeasured outcome")]
    for pat, label in language:
        for hit in _unnegated(pat, plain):
            advice.problems.append(f"{loc}: {label} wording '{hit}'")
    if section != "questions_for_humans" and (m := SAFETY_ASSERT.search(plain)):
        if re.search(r"safe|risk|harm|danger|hazard", m.group(0), re.I):
            advice.problems.append(f"{loc}: asserts absence of risk '{m.group(0)}'")
    if m := SOURCES.search(text):
        sources.problems.append(f"{loc}: source or URL '{m.group(0)}'")
    for hit in _unnegated(SOURCE_WORDS, plain):
        sources.problems.append(f"{loc}: outside source '{hit}'")
    if m := MARKUP.search(text):
        sources.problems.append(f"{loc}: markup '{m.group(0)[:30]}'")
    if m := LEAK.search(text):
        sources.problems.append(f"{loc}: prompt text or injection echo '{m.group(0)}'")


def _check_required(packet: EvidencePacket, interp: Interpretation, required: CheckOutcome) -> None:
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


_UNIT_AFTER_TOKEN = re.compile(
    r"\}\}\s*(minutes?|mins?|hours?|hrs?|seconds?|days?|weeks?|percent|%|percentage points?)\b",
    re.I,
)


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
    if m := _UNIT_AFTER_TOKEN.search(text):
        out.problems.append(f"{section}: unit '{m.group(1)}' typed after a value token")
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


CLAUSE_SPLIT = re.compile(r"[.;!?]|\b(?:while|whereas|but|and|although|though|yet)\b", re.I)
_NOT_DIRECTION = re.compile(
    r"\b(lower|upper)[- ]bounds?\b|\b(higher|lower)[- ]acuity\b|"
    r"\b(longer|shorter|more|less|higher|lower|fewer) than \d+(\.\d+)?\s*\w*",
    re.I,
)
_SLOT = " \u27e8v\u27e9 "  # placeholder for a value token inside prose


def _says(clause: str) -> str | None:
    lo, hi = LOWER_WORDS.search(clause), HIGHER_WORDS.search(clause)
    if lo and not hi:
        return "lower"
    if hi and not lo:
        return "higher"
    return None


def _check_direction(
    text: str,
    metric_facts: list[MetricFact],
    out: CheckOutcome,
    section: str,
    packet: EvidencePacket | None = None,
    *,
    whole_statement: bool = True,
) -> None:
    text = _NOT_DIRECTION.sub(" ", text)
    plain = TOKEN.sub(" value ", text)
    signed = [f for f in metric_facts if f.direction.startswith(("lower", "higher"))]
    unsigned = [f for f in metric_facts if f not in signed]
    if metric_facts and whole_statement:
        hedged = bool(HEDGE.search(text)) or any(
            f"{{{{{f.id}.change}}}}" in text.replace(" ", "") for f in unsigned
        )
        says_lower = bool(LOWER_WORDS.search(plain))
        says_higher = bool(HIGHER_WORDS.search(plain))
        if unsigned and not signed and (says_lower or says_higher) and not hedged:
            out.problems.append(
                f"{section}: describes a change for {[f.id for f in unsigned]}, whose 95% CI "
                "includes 0"
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
    if packet is None:
        return
    # Direction words in the clause right before a value token must match that fact.
    for fid, _, before in token_segments(text):
        fact = packet.fact(fid)
        if not isinstance(fact, MetricFact):
            continue
        clause = CLAUSE_SPLIT.split(before)[-1]
        said = _says(clause)
        if said is None or NEGATION.search(clause):
            continue
        if fact.direction.startswith(("lower", "higher")):
            if not fact.direction.startswith(said):
                out.problems.append(
                    f"{section}: '{clause.strip()[-40:]}' says {said}, but {fid} is "
                    f"{fact.direction}"
                )
        elif not HEDGE.search(clause):
            out.problems.append(f"{section}: describes {fid} as {said}; its 95% CI includes 0")
    # A clause without a token that names an outcome and a direction must be true
    # for at least one matching outcome (cited outcomes first, else the packet).
    for clause in CLAUSE_SPLIT.split(TOKEN.sub(_SLOT, _outcome_text(text, packet))):
        said = _says(clause)
        if _SLOT.strip() in clause or said is None or NEGATION.search(clause):
            continue
        if HEDGE.search(clause):
            continue
        fams = sorted(families_in(clause))
        wrong = []
        for fam in fams:
            pool = [f for f in metric_facts if fam in METRIC_FAMILIES.get(f.metric, set())]
            pool = pool or [
                f for f in packet.metric_facts if fam in METRIC_FAMILIES.get(f.metric, set())
            ]
            if pool and not any(f.direction.startswith(said) for f in pool):
                wrong.append(fam)
        # With several outcomes in one clause the direction word may belong to any of
        # them, so the clause fails only when it fits none of them.
        if wrong and len(wrong) == len(fams):
            out.problems.append(
                f"{section}: says {wrong[0]} was {said}, but no {wrong[0]} outcome is {said} in "
                "the model"
            )
