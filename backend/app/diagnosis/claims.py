"""Claim-level attribution of an answer to the evidence captured in a trace.

Whole-answer lexical overlap cannot tell a fabricated number from a paraphrase, or an answer that *contradicts*
a document from one drawn from it. This module splits the answer into claims and the evidence (retrieved
document content, tool outputs and tool errors) into units, keeping each unit's source and span. It then labels
every claim:

- ``supported``: its terms are covered by units of one source, with the same polarity and numbers.
- ``contradicted``: the closest evidence unit states the opposite (negation) or gives different numbers for the
  same statement. The evidence was available; the conclusion disagrees with it.
- ``unsupported``: it contains a number, or several terms, that occur in no evidence at all (missing evidence).
- ``abstention``: it reports missing information or an error rather than asserting a fact.
- ``undetermined``: none of the above can be established; the caller must not treat this as a failure.

Everything is deterministic and lexical. A paraphrase that shares too few terms with its evidence ends up
``undetermined``, never ``unsupported``, because only *novel* numbers and terms count as fabrication.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

ClaimStatus = Literal["supported", "contradicted", "unsupported", "abstention", "undetermined"]

STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have i if in is it its my of on or our that the their this to "
    "was we what when where which will with you your all any yes also just so than then there these those been being "
    "into about over under only such each more most other some very per via up out may must should would could get "
    "got me us they them he she his her here however within based please both either whether how who whom why "
    "same one's".split()
)
NEGATIONS = frozenset("not no never cannot non none nor neither exclude excluded excludes exclusion exclusions denied".split())
NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100,
}
ABSTENTION = re.compile(
    r"\b(no\b[^.]*\b(information|details?)\b|does not contain|do not contain|not available|could not find|couldn't find|"
    r"unable to|cannot answer|can't answer|i am sorry|i'm sorry|encountered an error|could you clarify|please clarify|"
    r"which (policy|topic) are you)",
    re.IGNORECASE,
)
# Sentence ends, semicolons, "but", and "and" when it starts a new clause (optionally an -ly adverb, then an
# auxiliary verb, pronoun or determiner). Purely grammatical: no domain vocabulary.
CLAUSE_SPLIT = re.compile(
    r"(?<=[.!?])\s+|;\s*|,?\s+\bbut\b\s+|,?\s+\band\b\s+(?=(?:\w+ly\s+)?(?:it|they|is|are|was|were|does|do|did|can|will|has|have|"
    r"the|a|an|our|your|its|their|this|these|that|those)\b)",
    re.IGNORECASE,
)
MAX_CLAIMS = 12
SUPPORT_COVERAGE = 0.6
CONTRADICTION_OVERLAP = 0.5
# Term-only fabrication is weak evidence (a paraphrase also uses new words), so it needs most terms to be novel and
# little of the claim to be covered by any source. Novel numbers are strong evidence on their own.
NOVEL_RATIO = 0.5
UNCOVERED = 0.4
UNIT_FACTORS = {"week": 7, "weeks": 7, "year": 12, "years": 12}  # weeks -> days, years -> months
TIME_UNITS = frozenset("day days week weeks month months year years hour hours minute minutes".split())


def _stem(word: str) -> str:
    for suffix in ("ability", "ings", "able", "ing", "ies", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def _normalize(text: str) -> str:
    text = text.replace("n't", " not").replace("’", "'")
    return re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)


def tokens(text: str) -> tuple[set[str], set[str], bool]:
    """(content terms, numbers in every equivalent form, negated) of a piece of text."""
    terms, quantities, negated = parse(text)
    return terms, {n for q in quantities for n in q}, negated


def covered(quantities: list[tuple[str, ...]], numbers: set[str]) -> bool:
    """Every quantity appears in ``numbers`` in at least one of its equivalent forms."""
    return all(any(n in numbers for n in q) for q in quantities)


def parse(text: str) -> tuple[set[str], list[tuple[str, ...]], bool]:
    """(content terms, quantities, negated). A quantity is (literal value, *equivalents), e.g. ("2", "14") for
    "two weeks", so a converted unit is not mistaken for a fabricated value."""
    words = re.findall(r"[a-z]+|\d+(?:\.\d+)?", _normalize(text).lower())
    quantities: list[tuple[str, ...]] = []
    terms: set[str] = set()
    negated = False
    for i, w in enumerate(words):
        value = float(w) if w[0].isdigit() else float(NUMBER_WORDS[w]) if w in NUMBER_WORDS else None
        if w in TIME_UNITS and i and (words[i - 1][0].isdigit() or words[i - 1] in NUMBER_WORDS):
            continue  # "two weeks": the unit is part of the quantity, compared numerically
        if value is not None:
            unit = words[i + 1] if i + 1 < len(words) else ""
            quantities.append((_num(value), _num(value * UNIT_FACTORS[unit])) if unit in UNIT_FACTORS else (_num(value),))
        elif w in NEGATIONS:
            negated = True
        elif w not in STOPWORDS and len(w) > 1:
            terms.add(_stem(w))
    return terms, quantities, negated


def _num(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


@dataclass
class Unit:
    source: str
    kind: str  # "document" | "tool_output" | "tool_error"
    span_id: str | None
    text: str
    terms: set[str]
    numbers: set[str]
    negated: bool
    expected: bool = False


@dataclass
class Claim:
    index: int
    text: str
    status: ClaimStatus
    terms: set[str] = field(default_factory=set)
    numbers: set[str] = field(default_factory=set)
    source: str | None = None
    span_id: str | None = None
    evidence_kind: str | None = None
    evidence_text: str | None = None
    expected_source: bool = False
    coverage: float = 0.0
    novel_terms: list[str] = field(default_factory=list)
    novel_numbers: list[str] = field(default_factory=list)
    reason: str = ""


# --- evidence units --------------------------------------------------------------------------------------------


def document_units(source: str, content: str, span_id: str | None, expected: bool) -> list[Unit]:
    """Sentences of a document. List items carry their lead-in line (``…does not cover:``) so they keep its polarity."""
    units: list[Unit] = []
    lead_in = ""
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            lead_in = ""
            continue
        bullet = re.match(r"^(?:[-*•]|\d+\.)\s+(.*)$", line)
        if line.startswith("#"):
            pieces = [re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", line.lstrip("#").strip())]  # "## 6. Tracking": 6 is not a value
            lead_in = ""
        elif bullet and lead_in:
            pieces = [f"{lead_in} {bullet.group(1)}"]
        else:
            text = bullet.group(1) if bullet else line
            pieces = [p for p in re.split(r"(?<=[.!?])\s+", text) if p]
            lead_in = pieces[-1].rstrip(":") if pieces and pieces[-1].endswith(":") else ""
        for piece in pieces:
            terms, numbers, negated = tokens(piece)
            if terms or numbers:
                units.append(Unit(source, "document", span_id, piece, terms, numbers, negated, expected))
    return units


def _flatten(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        out: list[str] = []
        for k, v in value.items():
            out += _flatten(v, f"{prefix} {str(k).replace('_', ' ')}".strip())
        return out
    if isinstance(value, list):
        return [x for v in value for x in _flatten(v, prefix)]
    return [f"{prefix} {value}".strip()]


def tool_units(name: str, output: Any, error: dict[str, Any] | None, span_id: str | None) -> list[Unit]:
    units: list[Unit] = []
    if error:
        text = f"{error.get('type', '')} {error.get('message', '')}".strip()
        terms, numbers, negated = tokens(text)
        units.append(Unit(name, "tool_error", span_id, text, terms, numbers, negated))
    if output is not None:
        parsed = output
        if isinstance(output, str):
            try:
                parsed = json.loads(output)
            except ValueError:
                parsed = output
        leaves = _flatten(parsed) if isinstance(parsed, (dict, list)) else [str(parsed)]
        for text in leaves[:200]:
            terms, numbers, negated = tokens(text)
            if terms or numbers:
                units.append(Unit(name, "tool_output", span_id, text, terms, numbers, negated))
    return units


# --- claims ----------------------------------------------------------------------------------------------------


def split_claims(answer: str) -> list[str]:
    parts = [p.strip(" ,.;:-") for p in CLAUSE_SPLIT.split(answer or "") if p and p.strip(" ,.;:-")]
    return parts[:MAX_CLAIMS]


def _cover(claim_terms: set[str], units: list[Unit]) -> tuple[float, list[Unit]]:
    """Greedy cover of the claim's terms by up to three units of one source."""
    remaining, used = set(claim_terms), []
    for _ in range(3):
        best = max(units, key=lambda u: len(remaining & u.terms), default=None)
        if best is None or not remaining & best.terms:
            break
        used.append(best)
        remaining -= best.terms
    return (1 - len(remaining) / len(claim_terms)) if claim_terms else 0.0, used


def attribute(answer: str, units: list[Unit]) -> list[Claim]:
    vocabulary = set().union(*(u.terms for u in units)) if units else set()
    all_numbers = set().union(*(u.numbers for u in units)) if units else set()
    by_source: dict[str, list[Unit]] = {}
    for u in units:
        by_source.setdefault(u.source, []).append(u)
    claims: list[Claim] = []
    for i, text in enumerate(split_claims(answer), start=1):
        terms, quantities, negated = parse(text)
        numbers = {n for q in quantities for n in q}
        claim = Claim(index=i, text=text, status="undetermined", terms=terms, numbers=numbers)
        claims.append(claim)
        if ABSTENTION.search(text):
            claim.status, claim.reason = "abstention", "reports missing information or an error"
            continue
        if len(terms) < 2 and not numbers:
            claim.reason = "too short to attribute"
            continue
        claim.novel_numbers = sorted(q[0] for q in quantities if not covered([q], all_numbers))
        claim.novel_terms = sorted(terms - vocabulary)

        # Closest single unit (for polarity / number conflicts) and best single-source cover (for support).
        def overlap(u: Unit) -> float:
            return len(terms & u.terms) / len(terms) if terms else 0.0

        ranked = sorted(units, key=lambda u: (overlap(u), u.expected), reverse=True)
        primary = ranked[0] if ranked else None
        # Rank sources by whether the units that cover the claim also carry its numbers, then by coverage.
        best_key, best_cover, best_units, best_source = (-1, -1.0, False), 0.0, [], None
        for source, src_units in by_source.items():
            cov, used = _cover(terms, src_units)
            fits = bool(used) and covered(quantities, set().union(*(u.numbers for u in used)))
            key = (int(fits), cov, bool(used and used[0].expected))
            if key > best_key:
                best_key, best_cover, best_units, best_source = key, cov, used, source
        claim.coverage = round(best_cover, 3)

        if primary is not None and overlap(primary) >= CONTRADICTION_OVERLAP and len(terms & primary.terms) >= 2:
            near = [u for u in ranked if overlap(u) >= overlap(primary) - 0.1 and len(terms & u.terms) >= 2]
            if primary.negated != negated and all(u.negated != negated for u in near):
                _set_evidence(claim, "contradicted", primary,
                              "the closest evidence states the opposite" if primary.negated else "the closest evidence does not negate what the claim negates")
                continue
            if quantities and primary.numbers and not numbers & primary.numbers and not any(covered(quantities, u.numbers) for u in near):
                _set_evidence(claim, "contradicted", primary, "the closest evidence gives different numbers for the same statement")
                continue
        if claim.novel_numbers:
            claim.status, claim.reason = "unsupported", "contains value(s) that occur in no evidence"
            continue
        if len(claim.novel_terms) >= 2 and len(claim.novel_terms) / len(terms) >= NOVEL_RATIO and best_cover < UNCOVERED:
            claim.status, claim.reason = "unsupported", "contains terms that occur in no evidence"
            continue
        if best_units and best_cover >= SUPPORT_COVERAGE and len(terms) - round((1 - best_cover) * len(terms)) >= 2:
            source_numbers = set().union(*(u.numbers for u in by_source[best_source or ""]))
            lead = best_units[0]
            if lead.negated == negated and covered(quantities, source_numbers):
                _set_evidence(claim, "supported", lead, "its terms are covered by this source")
                continue
        claim.reason = "evidence neither clearly supports nor refutes it"
    return claims


def _set_evidence(claim: Claim, status: ClaimStatus, unit: Unit, reason: str) -> None:
    claim.status, claim.reason = status, reason
    claim.source, claim.span_id, claim.evidence_kind = unit.source, unit.span_id, unit.kind
    claim.evidence_text, claim.expected_source = unit.text, unit.expected
