"""Extended reliability evaluators and lexical matching vs factual checks."""

import json
import re
import uuid
from typing import Any
from dotenv import dotenv_values
from app.db.session import make_engine, make_sessionmaker
from app.models import EvaluationRun, EvaluationResult, DatasetVersionCase, Trace, Span

def extract_numbers(text: str) -> set[str]:
    """Extract numeric values, dollar amounts, and time periods."""
    if not isinstance(text, str):
        return set()
    found = set(re.findall(r"\$?\b\d+(?:\.\d+)?%?\b", text))
    return found

def evaluate_extended_reliability(run_id_str: str) -> dict[str, Any]:
    env = dotenv_values(".env")
    engine = make_engine(env["DEV_DATABASE_URL"])
    Session = make_sessionmaker(engine)

    with open("agents/customer_support_agent/scenarios/test_cases.json", "r") as f:
        scenario_map = {c["key"]: c for c in json.load(f)}

    metrics = {
        "total_cases": 0,
        "numeric_checks": {"evaluated": 0, "passed": 0, "failed": 0},
        "factual_claims": {"evaluated": 0, "passed": 0, "failed": 0},
        "expected_source": {"evaluated": 0, "passed": 0, "failed": 0},
        "tool_call_match": {"evaluated": 0, "passed": 0, "failed": 0},
        "schema_valid": {"evaluated": 0, "passed": 0, "failed": 0},
        "evidence_sufficiency": {"evaluated": 0, "passed": 0, "failed": 0},
        "context_selection_correct": {"evaluated": 0, "passed": 0, "failed": 0},
        "lexical_vs_factual": {
            "both_pass": 0,
            "both_fail": 0,
            "lexical_pass_factual_fail": 0,  # False positive of lexical
            "lexical_fail_factual_pass": 0,  # False negative of lexical (e.g. paraphrase)
        },
        "per_case_details": [],
    }

    with Session() as db:
        run = db.get(EvaluationRun, uuid.UUID(run_id_str))
        results = db.query(EvaluationResult).filter_by(run_id=run.id).all()
        metrics["total_cases"] = len(results)

        for r in results:
            case = db.get(DatasetVersionCase, r.case_id)
            sc = scenario_map.get(case.case_key, {})
            out = r.output or {}
            ans = out.get("answer") if isinstance(out, dict) else str(out)
            exp_ans = case.expected_output.get("answer") if isinstance(case.expected_output, dict) else str(case.expected_output or "")
            exp_srcs = case.expected_sources or []
            exp_tool = sc.get("metadata", {}).get("expected_tool")
            exp_failure = sc.get("metadata", {}).get("expected_failure")

            # 1. Output Schema Validation
            is_schema_valid = isinstance(out, dict) and "answer" in out and isinstance(out.get("answer"), (str, type(None)))
            if is_schema_valid:
                metrics["schema_valid"]["passed"] += 1
            else:
                metrics["schema_valid"]["failed"] += 1
            metrics["schema_valid"]["evaluated"] += 1

            # 2. Numeric Correctness
            exp_nums = extract_numbers(exp_ans)
            act_nums = extract_numbers(ans or "")
            num_ok = True
            if exp_nums:
                metrics["numeric_checks"]["evaluated"] += 1
                if exp_nums.issubset(act_nums):
                    metrics["numeric_checks"]["passed"] += 1
                else:
                    metrics["numeric_checks"]["failed"] += 1
                    num_ok = False

            # 3. Factual Claims
            fact_ok = True
            if exp_ans:
                metrics["factual_claims"]["evaluated"] += 1
                exp_words = set(re.findall(r"\b\w{4,}\b", exp_ans.lower()))
                act_words = set(re.findall(r"\b\w{4,}\b", (ans or "").lower()))
                overlap = exp_words & act_words
                claim_ratio = len(overlap) / max(len(exp_words), 1)
                if claim_ratio >= 0.70:
                    metrics["factual_claims"]["passed"] += 1
                else:
                    metrics["factual_claims"]["failed"] += 1
                    fact_ok = False

            # 4. Expected Source References
            if exp_srcs:
                metrics["expected_source"]["evaluated"] += 1
                act_src = out.get("source") if isinstance(out, dict) else None
                if act_src in exp_srcs:
                    metrics["expected_source"]["passed"] += 1
                else:
                    metrics["expected_source"]["failed"] += 1

            # 5. Tool Call Match
            if exp_tool:
                metrics["tool_call_match"]["evaluated"] += 1
                # Inspect trace spans
                tool_spans = db.query(Span).filter(
                    Span.trace_id == r.trace_id,
                    Span.kind == "tool"
                ).all()
                called_tools = [s.name for s in tool_spans]
                if exp_tool in called_tools:
                    metrics["tool_call_match"]["passed"] += 1
                else:
                    metrics["tool_call_match"]["failed"] += 1

            # 6. Evidence Sufficiency
            retrieval_spans = db.query(Span).filter(
                Span.trace_id == r.trace_id,
                Span.name == "knowledge-retrieval"
            ).all()
            has_sufficient_evidence = False
            if retrieval_spans:
                metrics["evidence_sufficiency"]["evaluated"] += 1
                for s in retrieval_spans:
                    docs = (s.attributes or {}).get("documents", [])
                    if docs and any(len(d.get("content", "")) > 20 for d in docs):
                        has_sufficient_evidence = True
                        break
                if has_sufficient_evidence:
                    metrics["evidence_sufficiency"]["passed"] += 1
                else:
                    metrics["evidence_sufficiency"]["failed"] += 1

            # 7. Context Selection Correctness
            context_spans = db.query(Span).filter(
                Span.trace_id == r.trace_id,
                Span.name == "context-selection"
            ).all()
            if context_spans and exp_srcs:
                metrics["context_selection_correct"]["evaluated"] += 1
                sel_src = (context_spans[0].output or {}).get("source")
                if sel_src in exp_srcs:
                    metrics["context_selection_correct"]["passed"] += 1
                else:
                    metrics["context_selection_correct"]["failed"] += 1

            # 8. Lexical Matching vs Factual Grounding
            lexical_passed = any(e.get("evaluator") == "normalized_answer_match" and e.get("passed") for e in (r.evaluations or []))
            factual_passed = fact_ok and num_ok and (not exp_failure)

            if lexical_passed and factual_passed:
                metrics["lexical_vs_factual"]["both_pass"] += 1
            elif not lexical_passed and not factual_passed:
                metrics["lexical_vs_factual"]["both_fail"] += 1
            elif lexical_passed and not factual_passed:
                metrics["lexical_vs_factual"]["lexical_pass_factual_fail"] += 1
            elif not lexical_passed and factual_passed:
                metrics["lexical_vs_factual"]["lexical_fail_factual_pass"] += 1

            metrics["per_case_details"].append({
                "case_key": case.case_key,
                "lexical_passed": lexical_passed,
                "factual_passed": factual_passed,
                "overall_passed": r.passed,
                "expected_failure": exp_failure,
            })

    return metrics


if __name__ == "__main__":
    import sys
    run_id = sys.argv[1] if len(sys.argv) > 1 else "2df1bcb2-f286-4b1e-b7b6-b72a57a505eb"
    res = evaluate_extended_reliability(run_id)
    print("Extended Reliability Metrics:")
    print(json.dumps({k: v for k, v in res.items() if k != "per_case_details"}, indent=2))
