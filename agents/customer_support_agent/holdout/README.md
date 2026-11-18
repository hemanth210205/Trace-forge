# Customer Support Holdout (M10, pre-registered)

**Status: pre-registered, same author. Not independently validated.**

These 27 cases were written on 2026-11-18, *before* any M10 change was made to diagnosis, retrieval or
experiment logic. The same author (Claude Code, working with the repository owner) wrote both the holdout and
the M10 implementation. That makes this a frozen, separately held set, but not an independent benchmark.
Treat results as a check against overfitting the development data (dataset v3), not as external validation.

## Frozen content

| Item | Value |
|---|---|
| File | `holdout_cases.json` (27 cases, 10 injected failures, 17 positive/behavioural controls) |
| SHA-256 of file bytes (LF line endings) | `20d0e2a5c7961bccd4fc197d5e7ede7c6dfdb779e074e2ab83a92a6ecc3ba1c5` |
| SHA-256 of canonical JSON (`sort_keys`, compact separators) | `a71f9ef77b639fc3ca126f7af9be0007bff1d44ec9d7a41bbcbb8919d0c5a72d` |
| Traceforge dataset | `Customer Support Holdout` (separate dataset; v1–v3 of `Customer Support QA Dataset` are untouched) |

`sync_holdout.py` refuses to sync if the canonical hash differs. Any edit after M10 work begins
invalidates the pre-registration and must be reported as such.

## Contamination rules

1. Development and tuning use only dataset v3 (`scenarios/test_cases.json`) and unit-test fixtures written
   independently of this file.
2. No threshold, lexicon, intent cue, template or experiment rule may be changed because of a holdout result.
   If a holdout run exposes a defect, it is reported as a holdout finding. A fix made afterwards must be
   evaluated on new data, not re-scored on this holdout as if it were still unseen.
3. The holdout is evaluated once, after Stages 2–4 and their development tests are complete.
4. Unit tests must not import or copy holdout questions.

## Grading

Each case's `expected_output` is a list of key facts. The holdout is scored with the existing evaluators:

```json
[{"type": "contains", "field": "answer", "name": "key_facts"},
 {"type": "source_match", "name": "source_retrieval_match"},
 {"type": "required_fields", "fields": ["answer"], "name": "required_answer_field"}]
```

`contains` matches case-insensitively and requires every listed fact, so differently worded correct answers
pass and answers that miss a fact fail. `source_match` requires every expected source to be retrieved, so
multi-intent cases need every relevant document. It does not apply to cases with no expected sources
(ambiguous, irrelevant and tool-only cases).

`metadata.expected_failure` is the injected-failure label used to score diagnosis. It is `null` for positive
and behavioural controls.

## Pre-registered semantics of new injection modes

Two holdout cases use modes that do not exist yet. Their behaviour is fixed here so the implementation cannot
be shaped to the cases:

- `grounding_failure_contextual`: the agent retrieves and selects context normally, then answers the
  question's topic with a fabricated policy value (a number or condition that appears in no knowledge-base
  document). The correct information is in context but the answer is not drawn from it.
- `reasoning_failure_contextual`: the agent retrieves and selects the correct context normally. The answer
  reuses that context's own terms but reaches the opposite or a misapplied conclusion: an exclusion is stated
  as covered, a non-returnable item as returnable, or a value from a different clause of the same document is
  used.

The existing modes (`reasoning_failure`, `grounding_failure`, …) are unchanged, so dataset v3 behaves exactly as
before.

## Results (single run, 2026-11-18, after Stages 2–4 were complete)

Dataset `Customer Support Holdout` v1 in `traceforge_dev`. Source run `5e2b310c` (keyword baseline). Experiment
`224472ab` (keyword/best_rank vs multi_intent, identical inputs and injected failures in both arms).

| | Baseline | Candidate (multi_intent) |
|---|---:|---:|
| Pass rate (27 cases) | 25.9% (7) | 48.1% (13) |
| Positive/behavioural controls (17) | 41.2% | 76.5% |
| Injected failures detected (10) | 100% | 100% |
| Fixed / regressed | | 6 / 0 |

Diagnosis on the 10 injected failures: baseline arm 10/10 correct; candidate arm 9/10 (holdout-25 diagnosed as
grounding: under multi_intent retrieval the forced wrong context is a document that was never retrieved).

Holdout findings, **not** fixed (fixing them now would contaminate this holdout):

- holdout-02: "the window for sending an item back" matches no returns cue, so it is reported as unanswerable.
- holdout-07, holdout-10: "parcel … arrive in a day or two" and "regular delivery" carry only weak shipping cues,
  so multi_intent retrieves nothing (the baseline also failed).
- holdout-15: the answer templates cannot state the $4.99 fee for orders under $50 (both arms).

## Coverage

multi-intent (4), multi-intent with an unanswerable part (1), paraphrase (4), different wording (1),
boundary (5), tool execution failure (2), tool selection failure (1), grounding failure (3),
reasoning failure (3), context selection failure (1), ambiguous (1), irrelevant (1).
