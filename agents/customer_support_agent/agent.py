"""Deterministic Customer Support Agent for Traceforge Observability & Evaluation.

This agent implements a multi-step customer support workflow:
1. Query classification (query-classification span)
2. Knowledge retrieval with keyword, stemmed, and intent-weighted strategies (knowledge-retrieval span)
3. Context selection with best_rank, query_overlap, and intent_aligned strategies (context-selection span)
4. Tool execution (order-lookup-tool, refund-calculator-tool spans)
5. Answer generation (answer-generation LLM span)

It supports controlled failure injection for testing Traceforge's 8 diagnosis categories:
- normal: standard successful execution
- retrieval_failure_empty: retriever returns 0 documents
- retrieval_failure_irrelevant: retriever returns non-matching documents
- context_selection_failure: correct documents retrieved, wrong context chosen
- reasoning_failure: correct context chosen, opposite conclusion reached
- tool_selection_failure: wrong tool invoked
- tool_execution_failure: tool throws runtime exception
- grounding_failure: hallucinated answer ungrounded in context
- output_format_failure: missing required answer schema key
- insufficient_evidence: documents retrieved without captured content
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import traceforge

from .config import KB_DIR, get_settings
from .tools.order_lookup import order_lookup_tool
from .tools.refund_calculator import refund_calculator_tool

SAMPLE_PRICE_PER_TOKEN = 0.000002


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _terms(text: str, stemmed: bool) -> set[str]:
    words = _words(text)
    return {_stem(w) for w in words} if stemmed else set(words)


def load_knowledge_base(kb_dir: Path | None = None) -> list[dict[str, Any]]:
    """Loads knowledge base markdown documents from directory."""
    target_dir = kb_dir or KB_DIR
    docs: list[dict[str, Any]] = []
    files = {
        "kb/returns.md": target_dir / "returns.md",
        "kb/shipping.md": target_dir / "shipping.md",
        "kb/warranty.md": target_dir / "warranty.md",
    }
    for source, path in files.items():
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            doc_id = "doc-" + path.stem
            docs.append({"id": doc_id, "source": source, "content": content})
    return docs


KNOWLEDGE_BASE = load_knowledge_base()


# --- M10: multi-intent retrieval ---------------------------------------------------------------------------------

TOPIC_SOURCES = {"returns_policy": "kb/returns.md", "shipping_policy": "kb/shipping.md", "warranty_policy": "kb/warranty.md"}
TOPIC_LABELS = {"returns_policy": "returns", "shipping_policy": "shipping", "warranty_policy": "warranty"}
# Strong cues are the words classify_query already used; weak cues need a second signal. Terms that occur in only
# one knowledge-base document are added automatically (weight 0.5), so the lexicon is not hand-fitted to queries.
STRONG_CUES = {
    "returns_policy": {"return", "refund", "exchange", "restock"},
    "shipping_policy": {"ship", "shipp", "shipment", "carrier", "courier", "transit", "express", "track", "dispatch"},
    "warranty_policy": {"warranty", "guarantee", "defect", "defective", "broken"},
}
PHRASE_CUES = {"returns_policy": ("money back", "send back", "send it back")}
WEAK_CUES = {
    "returns_policy": {"tag", "packag", "packaging", "receipt", "unused"},
    "shipping_policy": {"delivery", "deliver", "arrive", "arriv"},
    "warranty_policy": {"repair", "replacement"},
}
STRONG, WEAK, INTENT_THRESHOLD = 1.0, 0.5, 1.0
QUERY_STOPWORDS = frozenset(
    "a an and are as at be by can could do does for from has have how i if in is it its me my of on or our that the "
    "their this to was we what when where which will with would you your there any all about after before i'm i've "
    "much many long also still please tell under over above below into than then just".split()
)
GENERIC_POLICY_WORDS = frozenset({"policy", "policies", "rule", "rules", "term", "terms"})


def _query_terms(text: str) -> list[str]:
    return [w for w in _words(text) if w not in QUERY_STOPWORDS and len(w) > 2]


def _norm(word: str) -> str:
    """Consistent normal form for intent matching ("takes"/"take" -> "tak", "shipping"/"ships" -> "ship"). Separate
    from ``_stem`` so the existing ``stemmed`` retrieval strategy is unchanged."""
    w = word.lower()
    if not w.endswith("ss"):
        for suffix in ("ing", "ed", "es", "s"):
            if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                w = w[: -len(suffix)]
                break
    w = w.rstrip("e") if len(w) > 3 else w
    if len(w) >= 4 and w[-1] == w[-2] and w[-1] not in "aeiou":
        w = w[:-1]
    return w


def _distinctive_terms() -> dict[str, set[str]]:
    doc_terms = {d["source"]: {_norm(w) for w in _query_terms(d["content"])} for d in KNOWLEDGE_BASE}
    out: dict[str, set[str]] = {}
    for topic, source in TOPIC_SOURCES.items():
        others = set().union(*(t for s, t in doc_terms.items() if s != source)) if len(doc_terms) > 1 else set()
        # A strong cue of another topic ("guaranteed" in shipping.md) is never evidence for this one.
        foreign_cues = {_norm(c) for t, cues in STRONG_CUES.items() if t != topic for c in cues}
        out[topic] = doc_terms.get(source, set()) - others - GENERIC_POLICY_WORDS - foreign_cues
    return out


DISTINCTIVE = _distinctive_terms()


def detect_intents(query: str) -> list[dict[str, Any]]:
    """Every policy intent in the query, in the order the query raises them, with the cues that triggered it."""
    q_lower = query.lower()
    words = _query_terms(query)
    found: list[dict[str, Any]] = []
    phrase_hits = {t: [p for p in PHRASE_CUES.get(t, ()) if p in q_lower] for t in TOPIC_SOURCES}
    for topic in TOPIC_SOURCES:
        score, cues, first, has_cue = 0.0, [], len(words), False
        strong, weak = {_norm(c) for c in STRONG_CUES[topic]}, {_norm(c) for c in WEAK_CUES[topic]}
        distinctive_hits = 0
        for i, w in enumerate(words):
            n = _norm(w)
            if n in strong or n in weak:
                weight = STRONG if n in strong else WEAK
                has_cue = True
            elif n in DISTINCTIVE[topic]:
                weight, distinctive_hits = WEAK, distinctive_hits + 1
            else:
                continue
            score += weight
            cues.append(w)
            first = min(first, i)
        for phrase in phrase_hits[topic]:
            score += STRONG
            cues.append(phrase)
            has_cue = True
            first = min(first, len(_query_terms(q_lower.split(phrase)[0])))
        if not has_cue and distinctive_hits < 2:
            continue  # one document-specific word alone ("gift") is not an intent
        if score >= INTENT_THRESHOLD:
            found.append({"intent": topic, "score": round(score, 3), "cues": list(dict.fromkeys(cues)), "position": first})
    found.sort(key=lambda f: (f["position"], -f["score"]))
    return found


def _intent_relevance(doc: dict[str, Any], intent: dict[str, Any], query: str) -> float:
    """How relevant a document is to one intent: topic match plus overlap with the query's own terms."""
    doc_terms = _terms(doc["content"], True)
    q_terms = {_stem(w) for w in _query_terms(query)}
    cue_terms = {_stem(c) for c in intent["cues"] if " " not in c}
    topic = 1.0 if TOPIC_SOURCES[intent["intent"]] == doc["source"] else 0.0
    cue_hit = len(cue_terms & doc_terms) / max(len(cue_terms), 1)
    return round(topic + 0.3 * cue_hit + 0.2 * len(q_terms & doc_terms) / max(len(q_terms), 1), 3)


def retrieve_multi_intent(query: str) -> list[dict[str, Any]]:
    """One best document per detected intent. A strong document for one intent cannot displace another intent's
    document, and nothing is retrieved for a query with no recognisable policy intent."""
    out: list[dict[str, Any]] = []
    for intent in detect_intents(query):
        ranked = sorted(KNOWLEDGE_BASE, key=lambda d: _intent_relevance(d, intent, query), reverse=True)
        if not ranked:
            continue
        best = ranked[0]
        score = _intent_relevance(best, intent, query)
        if score < 1.0:  # no document is about this topic
            continue
        if any(d["id"] == best["id"] for d in out):
            continue
        out.append({**best, "score": score, "intent": intent["intent"], "matched_cues": intent["cues"]})
    return out


@traceforge.observe(name="knowledge-retrieval", kind="retrieval")
def retrieve_documents(
    query: str,
    k: int = 2,
    strategy: str = "keyword",
    omit_content: bool = False,
    force_empty: bool = False,
    force_irrelevant: bool = False,
) -> list[dict[str, Any]]:
    """Retrieves documents matching query terms and records retrieval span with Traceforge."""
    span = traceforge.current_span()

    if force_empty:
        if span:
            span.add_documents([])
        return []

    if force_irrelevant:
        irrelevant = [d for d in KNOWLEDGE_BASE if "returns" not in d["source"] and "shipping" not in d["source"]]
        if span:
            span.add_documents(
                [
                    {
                        "id": d["id"],
                        "source": d["source"],
                        "score": 0.25,
                        "content": "" if omit_content else d["content"],
                    }
                    for d in irrelevant
                ]
            )
        return irrelevant

    q_lower = query.lower()
    scored: list[dict[str, Any]] = []

    if strategy == "multi_intent":
        top = retrieve_multi_intent(query)
        if span is not None:
            span.add_documents(
                [
                    {"id": d["id"], "source": d["source"], "score": d["score"], "intent": d["intent"],
                     "matched_cues": d["matched_cues"], "content": "" if omit_content else d["content"]}
                    for d in top
                ]
            )
        return top

    if strategy == "intent_weighted":
        # Candidate enhanced retriever: weights domain topic keywords heavily
        for doc in KNOWLEDGE_BASE:
            score = 0.1
            src = doc["source"]
            if any(w in q_lower for w in ("return", "refund", "tag", "original packaging")) and "returns" in src:
                score += 0.90
            elif "warranty" in q_lower and "warranty" in src:
                score += 0.90
            elif any(w in q_lower for w in ("ship", "carrier", "transit", "express")) and "shipping" in src:
                score += 0.90
            elif "delivery" in q_lower and not any(r in q_lower for r in ("return", "refund")) and "shipping" in src:
                score += 0.90

            query_terms = _terms(query, True)
            doc_terms = _terms(doc["content"], True)
            overlap = query_terms & doc_terms
            if overlap:
                score += len(overlap) / max(len(query_terms), 1) * 0.2
            scored.append({**doc, "score": round(score, 3)})
    else:
        stemmed = strategy == "stemmed"
        query_terms = _terms(query, stemmed)
        for doc in KNOWLEDGE_BASE:
            doc_terms = _terms(doc["content"], stemmed)
            overlap = query_terms & doc_terms
            if overlap:
                score = round(len(overlap) / max(len(query_terms), 1), 3)
                scored.append({**doc, "score": score})

    scored.sort(key=lambda d: d.get("score", 0.0), reverse=True)
    top_k = scored[:k]

    if span is not None:
        docs_to_record = []
        for d in top_k:
            doc_entry: dict[str, Any] = {
                "id": d["id"],
                "source": d["source"],
                "score": d.get("score", 0.0),
            }
            if not omit_content:
                doc_entry["content"] = d["content"]
            else:
                doc_entry["content"] = ""
            docs_to_record.append(doc_entry)
        span.add_documents(docs_to_record)

    return top_k


def classify_query(query: str) -> dict[str, Any]:
    """Classifies user query into intent and extracted entities."""
    with traceforge.span("query-classification", kind="custom", input={"query": query}) as span:
        q_lower = query.lower()
        order_match = re.search(r"\b(ORD-?\d+)\b", query, re.IGNORECASE)

        if order_match:
            intent = "order_inquiry"
            entity = order_match.group(1).upper()
        elif "refund" in q_lower and ("calculate" in q_lower or "deduction" in q_lower or "fee" in q_lower):
            intent = "refund_calculation"
            entity = None
        elif any(w in q_lower for w in ("warranty", "guarantee", "defect", "broken")):
            intent = "warranty_policy"
            entity = None
        elif any(w in q_lower for w in ("ship", "delivery", "carrier", "transit", "arrive", "express")):
            intent = "shipping_policy"
            entity = None
        elif any(w in q_lower for w in ("return", "refund", "exchange", "money back", "tag")):
            intent = "returns_policy"
            entity = None
        else:
            intent = "general_inquiry"
            entity = None

        result = {"intent": intent, "entity": entity}
        if span:
            span.set_output(result)
        return result


def select_context(
    retrieved_docs: list[dict[str, Any]],
    query: str,
    strategy: str = "best_rank",
    force_wrong: bool = False,
) -> dict[str, Any] | None:
    """Selects primary context document from retrieved candidates."""
    with traceforge.span(
        "context-selection",
        kind="custom",
        input={"retrieved_count": len(retrieved_docs), "strategy": strategy, "force_wrong": force_wrong},
    ) as span:
        if not retrieved_docs:
            if span:
                span.set_output(None)
            return None

        q_lower = query.lower()

        if force_wrong:
            # Deliberately pick a non-matching document for the query topic
            if "warranty" in q_lower or "guarantee" in q_lower or "defect" in q_lower:
                wrong = [d for d in retrieved_docs if "warranty" not in d["source"]]
                if not wrong:
                    # If not retrieved, pull shipping doc as wrong context
                    shipping_doc = next((d for d in KNOWLEDGE_BASE if "shipping" in d["source"]), retrieved_docs[-1])
                    wrong = [shipping_doc]
                chosen = wrong[0]
            elif any(w in q_lower for w in ("shipping", "transit", "delivery", "carrier", "arrive")):
                wrong = [d for d in retrieved_docs if "shipping" not in d["source"]]
                chosen = wrong[0] if wrong else retrieved_docs[-1]
            else:
                wrong = [d for d in retrieved_docs if "returns" not in d["source"]]
                chosen = wrong[0] if wrong else retrieved_docs[-1]
        elif strategy == "intent_aligned":
            # Candidate improved selector: aligns document source with query intent
            if any(w in q_lower for w in ("return", "refund", "tag")):
                matching = [d for d in retrieved_docs if "returns" in d["source"]]
            elif "warranty" in q_lower:
                matching = [d for d in retrieved_docs if "warranty" in d["source"]]
            elif any(w in q_lower for w in ("ship", "delivery", "express", "transit")):
                matching = [d for d in retrieved_docs if "shipping" in d["source"]]
            else:
                matching = []
            chosen = matching[0] if matching else retrieved_docs[0]
        elif strategy == "query_overlap":
            q_terms = _terms(query, True)
            chosen = max(retrieved_docs, key=lambda d: len(q_terms & _terms(d.get("content", ""), True)))
        else:
            chosen = retrieved_docs[0]

        if span:
            span.set_output({"selected_doc_id": chosen.get("id"), "source": chosen.get("source")})
        return chosen


def select_contexts_multi(retrieved_docs: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """multi_intent context strategy: keeps one document per intent, preserving each one's intent attribution."""
    with traceforge.span(
        "context-selection", kind="custom",
        input={"retrieved_count": len(retrieved_docs), "strategy": "multi_intent", "force_wrong": False},
    ) as span:
        chosen = [d for d in retrieved_docs if d.get("intent")] or retrieved_docs[:1]
        if span:
            span.set_output({
                "selected_doc_ids": [d.get("id") for d in chosen],
                "sources": [d.get("source") for d in chosen],
                "intents": [d.get("intent") for d in chosen],
                "selected_doc_id": chosen[0].get("id") if chosen else None,
                "source": chosen[0].get("source") if chosen else None,
            })
        return chosen


NO_INFORMATION = "I am sorry, but the retrieved documentation does not contain sufficient details to answer that question."

# Pre-registered injection semantics (holdout/README.md). Fabricated values occur in no knowledge-base document.
FABRICATED_ANSWERS = {
    "returns_policy": "Items may be returned within 90 days of delivery for a full refund back to the original payment method.",
    "shipping_policy": "Standard shipping takes 9 to 11 business days and is free for every order.",
    "warranty_policy": "The warranty covers manufacturing defects for 25 years from the purchase date.",
}


def _topic_for(selected_doc_source: str | None, query: str) -> str | None:
    for topic, source in TOPIC_SOURCES.items():
        if selected_doc_source == source:
            return topic
    intents = detect_intents(query)
    return intents[0]["intent"] if intents else None


def _misreasoned_answer(topic: str, query: str) -> str:
    """Reuses the context's own terms but reaches the opposite or a misapplied conclusion."""
    if topic == "warranty_policy":
        return "Yes, the warranty covers accidental damage, liquid spills and cracked screens."
    if topic == "returns_policy":
        return "Yes, every item, including gift cards, digital downloads and clearance merchandise, can be returned for a full refund."
    if "express" in query.lower():
        return "Express shipping takes 3 to 5 business days."
    return "Standard shipping takes 1 to 2 business days."


def _policy_sentence(topic: str, query: str) -> str:
    """The single-intent answer templates, per topic, without the single-context fallbacks."""
    q_lower = query.lower()
    if topic == "returns_policy":
        if "tags" in q_lower:
            return "Items must have original packaging and tags to be returned."
        return "Items may be returned within 30 days of delivery for a full refund back to the original payment method."
    if topic == "shipping_policy":
        if "express" in q_lower:
            return "Express shipping takes 1 to 2 business days and costs $14.99."
        return "Standard shipping takes 3 to 5 business days and is free for orders over $50."
    return "The warranty covers manufacturing defects for 1 year and strictly does not cover accidental damage."


def _query_clauses(query: str) -> list[str]:
    parts = re.split(r"\?|;|,?\s+\band\b\s+|,\s*\b(?:but|also)\b\s+|\.\s+", query)
    return [p.strip(" ,.") for p in parts if p and p.strip(" ,.")]


# Injected failures that replace the generated text; retrieval/selection injections still go through synthesis.
GENERATION_INJECTIONS = frozenset({"grounding_failure", "hallucinate", "reasoning_failure", "incorrect_reasoning",
                                   "grounding_failure_contextual", "reasoning_failure_contextual"})
ANAPHORA = frozenset({"it", "they", "them", "this", "that", "these", "those", "one"})


def _clause_unanswerable(clause: str) -> bool:
    """A clause of a multi-part question that no policy topic covers. A clause with any topic signal, or one that
    refers back to an earlier part ("how long does it take"), belongs to an answered part."""
    terms = _query_terms(clause)
    if len(terms) < 2 or set(_words(clause)) & ANAPHORA:
        return False
    all_cues = {_norm(c) for cues in (*STRONG_CUES.values(), *WEAK_CUES.values()) for c in cues}
    if any(_norm(w) in all_cues for w in terms):
        return False
    # Same rule as detect_intents: one document-specific word alone is not a topic signal.
    return not any(sum(_norm(w) in DISTINCTIVE[t] for w in terms) >= 2 for t in DISTINCTIVE)


def generate_multi_intent_answer(
    query: str,
    selected_docs: list[dict[str, Any]],
    tool_result: dict[str, Any] | None,
    tool_error: str | None,
    generation_mode: str = "normal",
    content_available: bool = True,
) -> str:
    """Answers each detected intent from its own document and says which parts cannot be answered."""
    first = selected_docs[0] if selected_docs else None
    if generation_mode in GENERATION_INJECTIONS or tool_result or tool_error or not content_available:
        return generate_answer_text(query, (first or {}).get("content") if content_available else None, tool_result,
                                    tool_error, generation_mode, (first or {}).get("source"))
    intents = detect_intents(query)
    if not intents:
        if set(_words(query)) & GENERIC_POLICY_WORDS:
            return ("Could you clarify which policy you are asking about? I can answer questions about returns, "
                    "shipping and warranty.")
        return NO_INFORMATION
    by_intent = {d.get("intent"): d for d in selected_docs if d.get("intent")}
    parts: list[str] = []
    for intent in intents:
        topic = intent["intent"]
        if topic in by_intent:
            parts.append(_policy_sentence(topic, query))
        else:
            parts.append(f"I could not find {TOPIC_LABELS[topic]} policy information to answer that part of your question.")
    for clause in _query_clauses(query):
        if _clause_unanswerable(clause):
            parts.append(f"I could not find information about \"{clause}\" in our policy documents.")
    return " ".join(dict.fromkeys(parts))


def generate_answer_text(
    query: str,
    selected_context: str | None,
    tool_result: dict[str, Any] | None,
    tool_error: str | None,
    generation_mode: str = "normal",
    selected_doc_source: str | None = None,
) -> str:
    """Produces deterministic answer string according to policy and generation mode."""
    if generation_mode in ("grounding_failure", "hallucinate"):
        return "You can get a refund anytime within 365 days by visiting our secret downtown warehouse."

    if generation_mode in ("reasoning_failure", "incorrect_reasoning"):
        return "Yes, our warranty covers all accidental damage and liquid drops without exception."

    if generation_mode in ("grounding_failure_contextual", "reasoning_failure_contextual"):
        topic = _topic_for(selected_doc_source, query) if selected_context else None
        if topic is None:
            return NO_INFORMATION
        if generation_mode == "grounding_failure_contextual":
            return FABRICATED_ANSWERS[topic]
        return _misreasoned_answer(topic, query)

    if tool_error:
        return f"I encountered an error looking up that information: {tool_error}"

    if tool_result:
        carrier = tool_result.get("carrier")
        status = tool_result.get("status")
        destination = tool_result.get("destination")
        if carrier and destination:
            return f"Your order is {status} via {carrier}. Destination: {destination}."
        if "estimated_delivery" in tool_result:
            return f"Order {tool_result.get('order_id')} is currently {status}. Estimated delivery date is {tool_result.get('estimated_delivery')}."
        if "refund_amount" in tool_result:
            return f"Calculated refund amount is ${tool_result['refund_amount']:.2f} ({tool_result.get('eligibility_status')})."

    if selected_context:
        q_lower = query.lower()

        # Route answer generation based on the selected context document
        if "returns" in (selected_doc_source or "") or "Returns and Refund Policy" in selected_context:
            if "missing tags" in q_lower or "tags" in q_lower:
                return "Items must have original packaging and tags to be returned."
            return "Items may be returned within 30 days of delivery for a full refund back to the original payment method."

        if "shipping" in (selected_doc_source or "") or "Shipping and Delivery Policy" in selected_context:
            if any(k in q_lower for k in ("warranty", "accidental", "defect", "hardware")):
                # Case 04 context selection failure: model was fed shipping context for a warranty query
                return "Based on the provided shipping policy, standard domestic delivery takes 3 to 5 business days. No warranty policy information is available in this context."
            if "express" in q_lower:
                return "Express shipping takes 1 to 2 business days and costs $14.99."
            return "Standard shipping takes 3 to 5 business days and is free for orders over $50."

        if "warranty" in (selected_doc_source or "") or "Warranty Policy" in selected_context:
            return "The warranty covers manufacturing defects for 1 year and strictly does not cover accidental damage."

        return f"Based on our policy documentation: {selected_context[:120]}..."

    # When context is empty or missing (e.g. insufficient_evidence)
    return "I am sorry, but the retrieved documentation does not contain sufficient details to answer that question."


def execute_agent_task(
    input_data: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Core deterministic agent execution function instrumented with Traceforge SDK."""
    config = config or {}
    query = input_data.get("question") or input_data.get("query") or ""
    override_mode = input_data.get("override_mode") or config.get("override_mode", "normal")
    retriever_strategy = config.get("retriever_strategy", "keyword")
    context_strategy = config.get("context_strategy", "best_rank")

    with traceforge.span(
        "customer-support-agent",
        kind="agent",
        input=input_data,
        metadata={"config": config, "override_mode": override_mode},
    ):
        classification = classify_query(query)
        tool_result = None
        tool_error = None

        # Tool execution branch
        if override_mode == "tool_selection_failure":
            tool_result = refund_calculator_tool(order_amount=50.0, return_eligibility=True)
        elif classification["intent"] == "order_inquiry" and classification["entity"]:
            trigger_fail = override_mode == "tool_execution_failure"
            order_id = classification["entity"]
            try:
                tool_result = order_lookup_tool(order_id, trigger_failure=trigger_fail)
            except Exception as exc:
                tool_error = str(exc)
        elif classification["intent"] == "refund_calculation":
            trigger_fail = override_mode == "tool_execution_failure"
            try:
                tool_result = refund_calculator_tool(
                    order_amount=input_data.get("order_amount", 100.0),
                    return_eligibility=input_data.get("return_eligibility", True),
                    applicable_deductions=input_data.get("deductions", 0.0),
                    trigger_failure=trigger_fail,
                )
            except Exception as exc:
                tool_error = str(exc)

        # Retrieval phase
        omit_content = override_mode == "insufficient_evidence"
        force_empty = override_mode == "retrieval_failure_empty"
        force_irrelevant = override_mode == "retrieval_failure_irrelevant"

        retrieved = retrieve_documents(
            query=query,
            k=2,
            strategy=retriever_strategy,
            omit_content=omit_content,
            force_empty=force_empty,
            force_irrelevant=force_irrelevant,
        )

        # Context selection phase
        force_wrong_context = override_mode == "context_selection_failure"
        multi = context_strategy == "multi_intent" and not force_wrong_context
        if multi:
            selected_docs = select_contexts_multi(retrieved, query)
            selected_doc = selected_docs[0] if selected_docs else None
        else:
            selected_doc = select_context(
                retrieved_docs=retrieved,
                query=query,
                strategy=context_strategy,
                force_wrong=force_wrong_context,
            )
            selected_docs = [selected_doc] if selected_doc else []

        # In insufficient_evidence mode, simulate agent lacking content
        if override_mode == "insufficient_evidence":
            selected_content = None
        elif multi:
            selected_content = "\n\n".join(d.get("content", "") for d in selected_docs) or None
        else:
            selected_content = selected_doc.get("content") if selected_doc else None

        selected_doc_source = selected_doc.get("source") if selected_doc else None

        # Answer generation phase
        prompt = (
            f"Query: {query}\n"
            f"Context: {selected_content}\n"
            f"Tool Result: {tool_result}\n"
            f"Tool Error: {tool_error}\n"
            f"Mode: {override_mode}"
        )
        with traceforge.span("answer-generation", kind="llm", input={"prompt": prompt}) as llm_span:
            if multi:
                answer_text = generate_multi_intent_answer(
                    query=query,
                    selected_docs=selected_docs,
                    tool_result=tool_result,
                    tool_error=tool_error,
                    generation_mode=override_mode,
                    content_available=override_mode != "insufficient_evidence",
                )
            else:
                answer_text = generate_answer_text(
                    query=query,
                    selected_context=selected_content,
                    tool_result=tool_result,
                    tool_error=tool_error,
                    generation_mode=override_mode,
                    selected_doc_source=selected_doc_source,
                )

            prompt_tokens = len(_words(prompt))
            completion_tokens = len(_words(answer_text))
            total_tokens = prompt_tokens + completion_tokens
            if llm_span:
                llm_span.set_llm(
                    model="customer-support-agent-v1",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    cost_usd=total_tokens * SAMPLE_PRICE_PER_TOKEN,
                )
                llm_span.set_output(answer_text)

        # Output format handling
        if override_mode == "output_format_failure":
            return {"reply_text": answer_text, "status": "incomplete"}

        result = {
            "answer": answer_text,
            "source": selected_doc_source,
            "tool_result": tool_result,
            "tool_error": tool_error,
        }
        if multi:
            result["sources"] = [d.get("source") for d in selected_docs]  # additive: every document the answer used
        return result


def run(
    query: str,
    override_mode: str = "normal",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """High-level runner that initializes Traceforge client, traces root, and flushes."""
    settings = get_settings()
    traceforge.init(api_key=settings.api_key, endpoint=settings.endpoint)

    input_payload = {"question": query, "override_mode": override_mode}
    with traceforge.trace(name="customer-support-task", metadata={"project_id": settings.project_id}) as trace:
        result = execute_agent_task(input_payload, config=config)
        trace.set_output(result)

    traceforge.flush()
    return result
