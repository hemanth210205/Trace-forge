"""Deterministic evaluators.

Each evaluator compares one case's actual output (and, for sources, the linked trace's retrieval spans)
with what the dataset version expects, and returns a structured, JSON-serializable outcome:

    {"evaluator", "type", "applicable", "passed", "score", "expected", "actual", "explanation", "supporting_refs"}

``applicable=False`` means the case gives the evaluator nothing to check (e.g. no expected output); such
outcomes do not count towards the case result. Evaluators never raise for bad *data* (they fail the case
with an explanation); an exception therefore signals a genuine scoring error and fails the run.
"""

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

MAX_SHOWN = 2000  # characters of expected/actual kept in an outcome


# --- configuration -----------------------------------------------------------------------------------------


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    field: Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)*$")] | None = None


class ExactMatch(_Config):
    type: Literal["exact_match"]


class NormalizedMatch(_Config):
    type: Literal["normalized_match"]
    ignore_punctuation: bool = True


class Contains(_Config):
    type: Literal["contains"]
    keywords: Annotated[list[Annotated[str, Field(min_length=1, max_length=500)]], Field(max_length=50)] | None = None
    mode: Literal["all", "any"] = "all"
    case_sensitive: bool = False


class JsonMatch(_Config):
    type: Literal["json_match"]
    allow_extra_keys: bool = True


class SourceMatch(_Config):
    type: Literal["source_match"]
    min_recall: Annotated[float, Field(ge=0, le=1)] = 1.0


class RequiredFields(_Config):
    type: Literal["required_fields"]
    fields: Annotated[list[Annotated[str, Field(min_length=1, max_length=200)]], Field(min_length=1, max_length=50)]


EvaluatorConfig = Annotated[
    ExactMatch | NormalizedMatch | Contains | JsonMatch | SourceMatch | RequiredFields, Field(discriminator="type")
]
EVALUATOR_TYPES = ("exact_match", "normalized_match", "contains", "json_match", "source_match", "required_fields")
_adapter: TypeAdapter[list[EvaluatorConfig]] = TypeAdapter(list[EvaluatorConfig])


def parse_configs(raw: list[dict[str, Any]]) -> list[_Config]:
    """Validates evaluator configs and fills in unique names. Raises ValueError for duplicates."""
    configs = _adapter.validate_python(raw)
    names = [c.name or c.type for c in configs]
    if len(set(names)) != len(names):
        raise ValueError("Evaluator names must be unique; set 'name' when using the same type twice.")
    for config, name in zip(configs, names):
        config.name = name
    return configs


# --- inputs -------------------------------------------------------------------------------------------------


@dataclass
class RetrievalSpan:
    span_id: str
    documents: list[dict[str, Any]]


@dataclass
class Evidence:
    """What the linked trace (if any, and only if it exists in the run's project) shows."""

    trace_id: str | None = None
    retrieval_spans: list[RetrievalSpan] = field(default_factory=list)
    error_span_ids: list[str] = field(default_factory=list)


@dataclass
class CaseData:
    expected_output: Any
    expected_sources: list[str]


@dataclass
class ResultData:
    output: Any
    execution_error: dict[str, Any] | None


_MISSING = object()


def _select(value: Any, path: str | None) -> Any:
    if path is None:
        return value
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return _MISSING
    return value


def _shown(value: Any) -> Any:
    if value is _MISSING:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= MAX_SHOWN:
        return value
    return text[:MAX_SHOWN] + "…"


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _normalize(text: str, ignore_punctuation: bool) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    if ignore_punctuation:
        text = "".join(" " if unicodedata.category(ch).startswith("P") else ch for ch in text)
    return re.sub(r"\s+", " ", text).strip()


def _outcome(config: _Config, *, passed: bool | None, score: float | None, expected: Any, actual: Any,
             explanation: str, refs: list[dict[str, str]], applicable: bool = True) -> dict[str, Any]:
    return {
        "evaluator": config.name,
        "type": config.type,  # type: ignore[attr-defined]
        "applicable": applicable,
        "passed": passed,
        "score": None if score is None else round(score, 4),
        "expected": _shown(expected),
        "actual": _shown(actual),
        "explanation": explanation,
        "supporting_refs": refs,
    }


def _trace_refs(evidence: Evidence) -> list[dict[str, str]]:
    if not evidence.trace_id:
        return []
    refs = [{"trace_id": evidence.trace_id}]
    refs += [{"trace_id": evidence.trace_id, "span_id": s, "reason": "error span"} for s in evidence.error_span_ids]
    return refs


# --- evaluators ---------------------------------------------------------------------------------------------


def evaluate(config: _Config, case: CaseData, result: ResultData, evidence: Evidence) -> dict[str, Any]:
    refs = _trace_refs(evidence)
    if config.type == "source_match":  # type: ignore[attr-defined]
        return _source_match(config, case, evidence)  # type: ignore[arg-type]
    if result.execution_error is not None:
        error = result.execution_error
        return _outcome(config, passed=False, score=0.0, expected=case.expected_output, actual=None, refs=refs,
                        explanation=f"The task raised {error.get('type', 'an error')}: {error.get('message', '')}".strip())
    actual = _select(result.output, config.field)
    if actual is _MISSING:
        return _outcome(config, passed=False, score=0.0, expected=case.expected_output, actual=None, refs=refs,
                        explanation=f"Field '{config.field}' is missing from the output.")
    handler = _HANDLERS[config.type]  # type: ignore[attr-defined]
    return handler(config, case, actual, refs)


def _not_applicable(config: _Config, why: str) -> dict[str, Any]:
    return _outcome(config, passed=None, score=None, expected=None, actual=None, explanation=why, refs=[], applicable=False)


def _exact(config: ExactMatch, case: CaseData, actual: Any, refs: list[dict[str, str]]) -> dict[str, Any]:
    if case.expected_output is None:
        return _not_applicable(config, "The case has no expected output.")
    ok = actual == case.expected_output and type(actual) is type(case.expected_output)
    return _outcome(config, passed=ok, score=1.0 if ok else 0.0, expected=case.expected_output, actual=actual, refs=refs,
                    explanation="Output equals the expected output exactly." if ok else "Output differs from the expected output.")


def _normalized(config: NormalizedMatch, case: CaseData, actual: Any, refs: list[dict[str, str]]) -> dict[str, Any]:
    if case.expected_output is None:
        return _not_applicable(config, "The case has no expected output.")
    a = _normalize(_as_text(actual), config.ignore_punctuation)
    e = _normalize(_as_text(case.expected_output), config.ignore_punctuation)
    ok = a == e
    how = "case, whitespace" + (" and punctuation" if config.ignore_punctuation else "")
    return _outcome(config, passed=ok, score=1.0 if ok else 0.0, expected=case.expected_output, actual=actual, refs=refs,
                    explanation=f"Matches after normalizing {how}." if ok else f"Differs even after normalizing {how}.")


def _contains(config: Contains, case: CaseData, actual: Any, refs: list[dict[str, str]]) -> dict[str, Any]:
    keywords = config.keywords
    if keywords is None:
        expected = case.expected_output
        if isinstance(expected, str) and expected:
            keywords = [expected]
        elif isinstance(expected, list) and expected and all(isinstance(k, str) and k for k in expected):
            keywords = expected
        else:
            return _not_applicable(config, "No keywords configured and the expected output is not a string or list of strings.")
    text = _as_text(actual)
    hay = text if config.case_sensitive else text.casefold()
    found = [k for k in keywords if (k if config.case_sensitive else k.casefold()) in hay]
    missing = [k for k in keywords if k not in found]
    score = len(found) / len(keywords)
    ok = not missing if config.mode == "all" else bool(found)
    explanation = (f"Contains {len(found)}/{len(keywords)} keyword(s)." if ok
                   else f"Missing {len(missing)} of {len(keywords)} keyword(s): " + ", ".join(repr(k) for k in missing[:5]))
    return _outcome(config, passed=ok, score=score, expected=keywords, actual=actual, refs=refs, explanation=explanation)


def _json(config: JsonMatch, case: CaseData, actual: Any, refs: list[dict[str, str]]) -> dict[str, Any]:
    expected = case.expected_output
    if not isinstance(expected, (dict, list)):
        return _not_applicable(config, "The expected output is not a JSON object or array.")
    if isinstance(actual, str):
        try:
            actual = json.loads(actual)
        except ValueError:
            return _outcome(config, passed=False, score=0.0, expected=expected, actual=actual, refs=refs,
                            explanation="Output is a string that is not valid JSON.")
    mismatches: list[str] = []
    total = _compare(expected, actual, "$", config.allow_extra_keys, mismatches)
    matched = total - len(mismatches)
    ok = not mismatches
    explanation = (f"All {total} expected value(s) match." if ok
                   else f"{len(mismatches)} of {total} expected value(s) differ: " + "; ".join(mismatches[:5]))
    return _outcome(config, passed=ok, score=matched / total if total else 1.0, expected=expected, actual=actual,
                    refs=refs, explanation=explanation)


def _compare(expected: Any, actual: Any, path: str, allow_extra: bool, out: list[str]) -> int:
    """Counts expected leaves under ``path``; appends a message per mismatch."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            out.append(f"{path} should be an object")
            return max(1, _leaves(expected))
        total = 0
        for key, value in expected.items():
            if key not in actual:
                out.append(f"{path}.{key} is missing")
                total += max(1, _leaves(value))
            else:
                total += _compare(value, actual[key], f"{path}.{key}", allow_extra, out)
        if not allow_extra:
            for key in actual.keys() - expected.keys():
                out.append(f"{path}.{key} is unexpected")
                total += 1
        return total
    if isinstance(expected, list):
        if not isinstance(actual, list):
            out.append(f"{path} should be an array")
            return max(1, _leaves(expected))
        if len(actual) != len(expected):
            out.append(f"{path} has {len(actual)} items, expected {len(expected)}")
        total = 0
        for i, value in enumerate(expected):
            if i < len(actual):
                total += _compare(value, actual[i], f"{path}[{i}]", allow_extra, out)
            else:
                total += max(1, _leaves(value))
        return max(total, 1)
    if expected != actual or type(expected) is not type(actual):
        out.append(f"{path} is {json.dumps(actual, default=str)[:80]}, expected {json.dumps(expected, default=str)[:80]}")
    return 1


def _leaves(value: Any) -> int:
    if isinstance(value, dict):
        return sum(max(1, _leaves(v)) for v in value.values())
    if isinstance(value, list):
        return sum(max(1, _leaves(v)) for v in value)
    return 1


def _required(config: RequiredFields, case: CaseData, actual: Any, refs: list[dict[str, str]]) -> dict[str, Any]:
    missing = [f for f in config.fields if _select(actual, f) in (_MISSING, None, "")]
    present = len(config.fields) - len(missing)
    ok = not missing
    return _outcome(config, passed=ok, score=present / len(config.fields), expected=config.fields, actual=actual, refs=refs,
                    explanation="All required fields are present." if ok else "Missing or empty: " + ", ".join(missing))


def _source_match(config: SourceMatch, case: CaseData, evidence: Evidence) -> dict[str, Any]:
    expected = list(dict.fromkeys(case.expected_sources))
    if not expected:
        return _not_applicable(config, "The case has no expected source references.")
    if not evidence.trace_id:
        return _outcome(config, passed=False, score=0.0, expected=expected, actual=None, refs=[],
                        explanation="No linked trace was found, so the retrieved sources cannot be verified.")
    refs = [{"trace_id": evidence.trace_id, "span_id": s.span_id, "reason": "retrieval span"} for s in evidence.retrieval_spans]
    retrieved: list[str] = []
    for span in evidence.retrieval_spans:
        for doc in span.documents:
            for key in ("source", "id"):
                value = doc.get(key)
                if isinstance(value, str) and value not in retrieved:
                    retrieved.append(value)
    if not evidence.retrieval_spans:
        return _outcome(config, passed=False, score=0.0, expected=expected, actual=[], refs=_trace_refs(evidence),
                        explanation="The linked trace has no retrieval spans with documents.")
    hits = [s for s in expected if s in retrieved]
    recall = len(hits) / len(expected)
    ok = recall >= config.min_recall
    missing = [s for s in expected if s not in hits]
    explanation = f"Retrieved {len(hits)}/{len(expected)} expected source(s) (recall {recall:.2f}, required {config.min_recall:.2f})."
    if missing:
        explanation += " Not retrieved: " + ", ".join(missing[:5]) + "."
    return _outcome(config, passed=ok, score=recall, expected=expected, actual=retrieved, refs=refs, explanation=explanation)


_HANDLERS = {
    "exact_match": _exact,
    "normalized_match": _normalized,
    "contains": _contains,
    "json_match": _json,
    "required_fields": _required,
}
