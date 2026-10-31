"""Deterministic evaluators (pure functions; no database)."""

import pytest

from app.services import evaluators as ev


def run(config: dict, expected=None, output=None, sources=(), error=None, evidence: ev.Evidence | None = None) -> dict:
    (parsed,) = ev.parse_configs([config])
    return ev.evaluate(parsed, ev.CaseData(expected, list(sources)), ev.ResultData(output, error), evidence or ev.Evidence())


def test_exact_match() -> None:
    assert run({"type": "exact_match"}, "Shipped.", "Shipped.")["passed"] is True
    miss = run({"type": "exact_match"}, "Shipped.", "shipped.")
    assert miss["passed"] is False and miss["score"] == 0 and miss["expected"] == "Shipped." and miss["actual"] == "shipped."
    assert run({"type": "exact_match"}, 1, True)["passed"] is False  # no bool/int confusion
    assert run({"type": "exact_match"}, {"a": [1, 2]}, {"a": [1, 2]})["passed"] is True
    assert run({"type": "exact_match"}, None, "x")["applicable"] is False


def test_normalized_match_and_field_selection() -> None:
    config = {"type": "normalized_match", "field": "answer"}
    assert run(config, "Your order is SHIPPED", {"answer": "  your   order is shipped!"})["passed"] is True
    strict = run({**config, "ignore_punctuation": False}, "shipped", {"answer": "shipped!"})
    assert strict["passed"] is False
    missing = run(config, "x", {"reply": "x"})
    assert missing["passed"] is False and "Field 'answer' is missing" in missing["explanation"]


def test_contains_keywords_all_and_any() -> None:
    config = {"type": "contains", "keywords": ["refund", "30 days"]}
    full = run(config, None, "Refunds within 30 days.")
    assert full["passed"] is True and full["score"] == 1
    partial = run(config, None, "Refunds are possible.")
    assert partial["passed"] is False and partial["score"] == 0.5 and "'30 days'" in partial["explanation"]
    assert run({**config, "mode": "any"}, None, "Refunds are possible.")["passed"] is True
    assert run({"type": "contains"}, ["parcelco"], "Shipped with ParcelCo")["passed"] is True  # keywords from expected
    assert run({"type": "contains"}, {"not": "keywords"}, "x")["applicable"] is False


def test_json_structural_match() -> None:
    expected = {"status": "shipped", "order": {"id": "A100", "items": [1, 2]}}
    ok = run({"type": "json_match"}, expected, {"status": "shipped", "order": {"id": "A100", "items": [1, 2]}, "extra": 1})
    assert ok["passed"] is True and ok["score"] == 1
    bad = run({"type": "json_match"}, expected, '{"status": "processing", "order": {"items": [1]}}')
    assert bad["passed"] is False and 0 < bad["score"] < 1
    assert "$.status" in bad["explanation"] and "$.order.id is missing" in bad["explanation"]
    strict = run({"type": "json_match", "allow_extra_keys": False}, {"a": 1}, {"a": 1, "b": 2})
    assert strict["passed"] is False and "$.b is unexpected" in strict["explanation"]
    assert run({"type": "json_match"}, expected, "not json")["explanation"] == "Output is a string that is not valid JSON."
    assert run({"type": "json_match"}, "plain", "plain")["applicable"] is False


def test_required_fields() -> None:
    config = {"type": "required_fields", "fields": ["answer", "meta.confidence"]}
    assert run(config, None, {"answer": "x", "meta": {"confidence": 0.9}})["passed"] is True
    missing = run(config, None, {"answer": "", "meta": {}})
    assert missing["passed"] is False and missing["score"] == 0 and "answer, meta.confidence" in missing["explanation"]


def test_source_match_uses_trace_retrieval_spans() -> None:
    evidence = ev.Evidence(trace_id="t1", retrieval_spans=[
        ev.RetrievalSpan("s1", [{"id": "kb-returns", "source": "kb/returns.md"}, {"source": "kb/warranty.md"}]),
    ])
    ok = run({"type": "source_match"}, sources=["kb/returns.md"], evidence=evidence)
    assert ok["passed"] is True and ok["actual"] == ["kb/returns.md", "kb-returns", "kb/warranty.md"]
    assert ok["supporting_refs"] == [{"trace_id": "t1", "span_id": "s1", "reason": "retrieval span"}]
    partial = run({"type": "source_match"}, sources=["kb/returns.md", "kb/shipping.md"], evidence=evidence)
    assert partial["passed"] is False and partial["score"] == 0.5 and "kb/shipping.md" in partial["explanation"]
    assert run({"type": "source_match", "min_recall": 0.5}, sources=["kb/returns.md", "kb/x.md"], evidence=evidence)["passed"]
    no_trace = run({"type": "source_match"}, sources=["kb/returns.md"])
    assert no_trace["passed"] is False and no_trace["supporting_refs"] == [] and "No linked trace" in no_trace["explanation"]
    assert run({"type": "source_match"}, sources=[])["applicable"] is False


def test_execution_errors_fail_with_explanation_and_error_span_refs() -> None:
    evidence = ev.Evidence(trace_id="t1", error_span_ids=["e1"])
    out = run({"type": "exact_match"}, "x", None, error={"type": "TimeoutError", "message": "upstream"}, evidence=evidence)
    assert out["passed"] is False and out["explanation"] == "The task raised TimeoutError: upstream"
    assert {"trace_id": "t1", "span_id": "e1", "reason": "error span"} in out["supporting_refs"]


@pytest.mark.parametrize(
    "configs",
    [[{"type": "llm_judge"}], [{"type": "contains", "unknown": 1}], [{"type": "required_fields", "fields": []}],
     [{"type": "exact_match"}, {"type": "exact_match"}], [{"type": "source_match", "min_recall": 2}]],
)
def test_invalid_configurations_are_rejected(configs: list) -> None:
    with pytest.raises(ValueError):
        ev.parse_configs(configs)


def test_same_type_twice_with_distinct_names() -> None:
    names = [c.name for c in ev.parse_configs([{"type": "contains", "keywords": ["a"]}, {"type": "contains", "name": "b", "keywords": ["b"]}])]
    assert names == ["contains", "b"]
