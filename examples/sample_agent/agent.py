"""Sample customer-support agent instrumented with the Traceforge SDK.

It is a real (small) program: keyword retrieval over a local knowledge base, an order-lookup tool, and a
deterministic *template* language model, so it runs offline and gives repeatable traces. Token counts are
the actual word counts of the text it processes; the cost uses the made-up rate in ``SAMPLE_PRICE_PER_TOKEN``
and is labelled as such in span metadata.

    pip install -e sdk/python
    set TRACEFORGE_API_KEY=tf_...            (a project API key from the Traceforge console)
    python examples/sample_agent/agent.py --endpoint http://127.0.0.1:8000

Exit status 0 means every trace was delivered; 1 means the SDK dropped or failed to deliver telemetry.
"""

import argparse
import asyncio
import json
import re
import sys

import traceforge

KNOWLEDGE_BASE = [
    {"id": "kb-shipping", "source": "kb/shipping.md",
     "content": "Orders ship within 2 business days. Express shipping arrives in 1-2 days after dispatch."},
    {"id": "kb-returns", "source": "kb/returns.md",
     "content": "Items can be returned within 30 days of delivery for a full refund to the original payment method."},
    {"id": "kb-warranty", "source": "kb/warranty.md",
     "content": "Electronics carry a 12 month warranty covering manufacturing defects."},
    {"id": "kb-payments", "source": "kb/payments.md",
     "content": "We accept card and bank transfer payments. Refunds take 5-7 business days to appear."},
]
ORDERS = {
    "A100": {"status": "shipped", "carrier": "ParcelCo", "eta_days": 2},
    "B200": {"status": "processing", "carrier": None, "eta_days": 4},
}
SAMPLE_PRICE_PER_TOKEN = 0.000002  # illustrative only; this agent calls no paid model
DEFAULT_QUESTIONS = [
    "Where is my order A100?",
    "How do refunds work for a return?",
    "What is the status of order Z999?",  # unknown order: the tool raises, the agent recovers
]


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _stem(word: str) -> str:
    """Naive suffix stripping ("orders" -> "order", "covering" -> "cover"); enough for the sample corpus."""
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _terms(text: str, stemmed: bool) -> set[str]:
    words = _words(text)
    return {_stem(w) for w in words} if stemmed else set(words)


# Configuration the agent supports (used by experiments). Defaults reproduce the original behaviour.
DEFAULT_CONFIG = {"retriever": "keyword", "context": "first"}


@traceforge.observe(name="kb-search", kind="retrieval")
def retrieve(question: str, k: int = 2, retriever: str = "keyword") -> list[dict]:
    stemmed = retriever == "stemmed"
    terms = _terms(question, stemmed)
    scored = []
    for doc in KNOWLEDGE_BASE:
        overlap = terms & _terms(doc["content"], stemmed)
        if overlap:
            scored.append({**doc, "score": round(len(overlap) / len(terms), 3)})
    top = sorted(scored, key=lambda d: d["score"], reverse=True)[:k]
    span = traceforge.current_span()
    if span is not None:
        span.add_documents({"id": d["id"], "source": d["source"], "score": d["score"], "content": d["content"]} for d in top)
    return top


@traceforge.observe(name="order-lookup", kind="tool")
async def lookup_order(order_id: str) -> dict:
    await asyncio.sleep(0.02)  # simulated I/O latency
    span = traceforge.current_span()
    if span is not None:
        span.set_tool("order-lookup", {"order_id": order_id})
    if order_id not in ORDERS:
        raise LookupError(f"order {order_id} not found")
    return ORDERS[order_id]


def template_llm(prompt: str) -> str:
    """Deterministic stand-in for a model: answers from the facts placed in the prompt."""
    facts = json.loads(prompt.split("FACTS:", 1)[1])
    if facts.get("order"):
        order = facts["order"]
        return f"Your order is {order['status']}" + (f" with {order['carrier']}." if order["carrier"] else ".")
    if facts.get("order_error"):
        return "I could not find that order. Please check the order number."
    if facts.get("documents"):
        return "According to our policy: " + facts["documents"][0]["content"]
    return "I'm not sure; let me connect you with a human agent."


async def generate(question: str, facts: dict) -> str:
    prompt = f"Answer the customer question.\nQUESTION: {question}\nFACTS:{json.dumps(facts)}"
    with traceforge.span("compose-answer", kind="llm", input={"prompt": prompt}) as span:
        await asyncio.sleep(0.01)
        answer = template_llm(prompt)
        prompt_tokens, completion_tokens = len(_words(prompt)), len(_words(answer))
        span.set_llm(
            model="sample-template-llm",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=(prompt_tokens + completion_tokens) * SAMPLE_PRICE_PER_TOKEN,
            temperature=0.0,
        )
        span.set_metadata(pricing="illustrative sample rate, not a real provider price")
        span.set_output(answer)
        return answer


async def answer(question: str, config: dict | None = None) -> str:
    """The agent itself; runs inside whatever trace is current (a request trace or an evaluation case).

    ``config`` keys: ``retriever`` ("keyword" | "stemmed") and ``context`` ("first": answer from the top-ranked
    document | "query_overlap": answer from the retrieved document sharing most words with the question)."""
    settings = {**DEFAULT_CONFIG, **(config or {})}
    unknown = set(settings) - set(DEFAULT_CONFIG)
    if unknown or settings["retriever"] not in ("keyword", "stemmed") or settings["context"] not in ("first", "query_overlap"):
        raise ValueError(f"unsupported agent configuration: {config}")
    with traceforge.span("support-agent", kind="agent", input=question, metadata={"config": settings}):
        documents = retrieve(question, retriever=settings["retriever"])
        if settings["context"] == "query_overlap" and documents:
            q = _terms(question, True)
            documents = sorted(documents, key=lambda d: len(q & _terms(d["content"], True)), reverse=True)
        facts: dict = {"documents": documents}
        match = re.search(r"\b([A-Z]\d{3})\b", question)
        if match:
            try:
                facts["order"] = await lookup_order(match.group(1))
            except LookupError as exc:
                facts["order_error"] = str(exc)  # recorded on the tool span; the agent recovers
        return await generate(question, facts)


async def handle(question: str, customer: dict) -> str:
    with traceforge.trace("support-request", input={"question": question, "customer": customer},
                          tags=["sample-agent"], user_id=customer["id"]) as trace:
        reply = await answer(question)
        trace.set_output({"answer": reply})
        trace.set_metadata(agent_version="sample-0.1")
        return reply


async def main(questions: list[str]) -> None:
    # The card number and API key below are fake; they show that the SDK redacts them before sending.
    customer = {"id": "cust-001", "email": "pat@example.com", "api_key": "fake-key-123",
                "note": "card on file sk-" + "x" * 24}
    answers = await asyncio.gather(*(handle(q, customer) for q in questions))
    for question, answer in zip(questions, answers):
        print(f"Q: {question}\nA: {answer}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--endpoint", default=None, help="Traceforge URL (default: TRACEFORGE_ENDPOINT or http://127.0.0.1:8000)")
    parser.add_argument("--api-key", default=None, help="Project API key (default: TRACEFORGE_API_KEY)")
    parser.add_argument("questions", nargs="*", default=DEFAULT_QUESTIONS)
    args = parser.parse_args()

    client = traceforge.init(api_key=args.api_key, endpoint=args.endpoint)
    if not client.enabled:
        sys.exit("No API key: pass --api-key or set TRACEFORGE_API_KEY.")
    asyncio.run(main(args.questions))
    delivered = client.shutdown(timeout=30)
    stats = client.stats()
    print(f"telemetry: {json.dumps(stats)}")
    sys.exit(0 if delivered and stats["dropped_items"] == 0 and stats["failed_batches"] == 0 else 1)
