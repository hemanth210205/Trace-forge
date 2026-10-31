"""Trace ingestion (project API key) and the console trace read API (session), on real PostgreSQL."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Span, Trace
from tests.conftest import ApiClient

INGEST = "/api/v1/ingest/batch"
T0 = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def iso(offset_ms: float = 0) -> str:
    return (T0 + timedelta(milliseconds=offset_ms)).isoformat()


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def trace(trace_id: uuid.UUID, **fields: Any) -> dict[str, Any]:
    return {"id": str(trace_id), "name": "support-request", "started_at": iso(0), "ended_at": iso(900),
            "status": "ok", **fields}


def span(trace_id: uuid.UUID, span_id: uuid.UUID | None = None, **fields: Any) -> dict[str, Any]:
    return {"id": str(span_id or uuid.uuid4()), "trace_id": str(trace_id), "name": "step", "kind": "custom",
            "started_at": iso(10), "ended_at": iso(50), "status": "ok", **fields}


@pytest.fixture
def project_key(alice: ApiClient) -> tuple[dict[str, Any], str]:
    project = alice.create_project("Agent", slug="agent")
    return project, alice.create_api_key(project["id"])["key"]


def full_trace() -> tuple[uuid.UUID, dict[str, Any]]:
    trace_id, root, llm, tool, retr = (uuid.uuid4() for _ in range(5))
    batch = {
        "traces": [trace(trace_id, input={"question": "Where is my order?"}, output={"answer": "Shipped."},
                         metadata={"agent_version": "1.2"}, tags=["prod", "support"], user_id="u-42")],
        "spans": [
            span(trace_id, root, name="agent", kind="agent", started_at=iso(0), ended_at=iso(900)),
            span(trace_id, retr, parent_span_id=str(root), name="kb-search", kind="retrieval",
                 attributes={"documents": [{"id": "doc-7", "source": "kb/shipping.md", "score": 0.91}]}),
            span(trace_id, tool, parent_span_id=str(root), name="order-lookup", kind="tool", status="error",
                 started_at=iso(60), ended_at=iso(200), input={"order_id": "A1"},
                 error={"type": "TimeoutError", "message": "upstream timed out"}),
            span(trace_id, llm, parent_span_id=str(root), name="answer", kind="llm", started_at=iso(210),
                 ended_at=iso(880), model="gpt-x", prompt_tokens=120, completion_tokens=30, cost_usd="0.0042"),
        ],
    }
    return trace_id, batch


def test_ingested_trace_is_readable_with_spans_and_aggregates(alice: ApiClient, client: ApiClient, project_key) -> None:
    project, key = project_key
    trace_id, batch = full_trace()
    response = client.post(INGEST, json=batch, headers=bearer(key))
    assert response.status_code == 202, response.text
    assert response.json() == {"accepted_traces": 1, "accepted_spans": 4}

    detail = alice.get(f"/api/v1/projects/{project['id']}/traces/{trace_id}").json()
    assert detail["name"] == "support-request" and detail["status"] == "ok"
    assert detail["duration_ms"] == 900
    assert (detail["span_count"], detail["error_count"], detail["total_tokens"]) == (4, 1, 150)
    assert detail["total_cost_usd"] == "0.004200"
    assert detail["input"] == {"question": "Where is my order?"} and detail["metadata"] == {"agent_version": "1.2"}
    assert detail["tags"] == ["prod", "support"] and detail["user_id"] == "u-42" and detail["has_trace_record"]
    by_name = {s["name"]: s for s in detail["spans"]}
    assert [s["name"] for s in detail["spans"]] == ["agent", "kb-search", "order-lookup", "answer"]  # by start
    assert by_name["answer"]["total_tokens"] == 150  # derived from prompt + completion
    assert by_name["order-lookup"]["error"] == {"type": "TimeoutError", "message": "upstream timed out", "stacktrace": None}
    assert by_name["kb-search"]["attributes"]["documents"][0]["source"] == "kb/shipping.md"
    assert by_name["kb-search"]["parent_span_id"] == by_name["agent"]["id"]
    assert by_name["agent"]["error"] is None and by_name["agent"]["input"] is None


def test_resending_a_batch_is_idempotent(client: ApiClient, project_key, db: Session) -> None:
    _, key = project_key
    trace_id, batch = full_trace()
    for _ in range(3):
        assert client.post(INGEST, json=batch, headers=bearer(key)).status_code == 202
    assert db.scalar(select(func.count()).select_from(Trace)) == 1
    assert db.scalar(select(func.count()).select_from(Span)) == 4
    assert db.scalar(select(Trace.span_count)) == 4


def test_spans_before_trace_record_create_a_placeholder_that_is_completed_later(
    alice: ApiClient, client: ApiClient, project_key
) -> None:
    project, key = project_key
    trace_id = uuid.uuid4()
    child = span(trace_id, parent_span_id=str(uuid.uuid4()), started_at=iso(100), ended_at=iso(150))
    assert client.post(INGEST, json={"spans": [child]}, headers=bearer(key)).status_code == 202
    url = f"/api/v1/projects/{project['id']}/traces/{trace_id}"
    placeholder = alice.get(url).json()
    assert placeholder["has_trace_record"] is False and placeholder["span_count"] == 1
    assert placeholder["status"] == "unset" and placeholder["ended_at"] is None

    assert client.post(INGEST, json={"traces": [trace(trace_id, name="late-record")]}, headers=bearer(key)).status_code == 202
    completed = alice.get(url).json()
    assert completed["name"] == "late-record" and completed["has_trace_record"] is True
    assert completed["span_count"] == 1 and completed["duration_ms"] == 900


def test_duplicate_ids_within_one_batch_keep_the_last(client: ApiClient, project_key, db: Session) -> None:
    _, key = project_key
    trace_id, span_id = uuid.uuid4(), uuid.uuid4()
    batch = {"traces": [trace(trace_id)], "spans": [span(trace_id, span_id, name="first"), span(trace_id, span_id, name="second")]}
    assert client.post(INGEST, json=batch, headers=bearer(key)).status_code == 202
    assert db.scalars(select(Span.name)).all() == ["second"]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda b: b["spans"][0].update(started_at="2026-10-30T12:00:00"), id="naive-datetime"),
        pytest.param(lambda b: b["spans"][0].update(ended_at=iso(-1)), id="ends-before-start"),
        pytest.param(lambda b: b["spans"][0].update(kind="database"), id="unknown-kind"),
        pytest.param(lambda b: b["spans"][0].update(prompt_tokens=-1), id="negative-tokens"),
        pytest.param(lambda b: b["spans"][0].update(input="x" * (64 * 1024 + 1)), id="oversized-input"),
        pytest.param(lambda b: b["spans"][0].update(metadata={"k": "x" * (16 * 1024)}), id="oversized-metadata"),
        pytest.param(lambda b: b["spans"][0].update(unexpected=True), id="unknown-field"),
        pytest.param(lambda b: b["spans"][0].update(parent_span_id=b["spans"][0]["id"]), id="own-parent"),
        pytest.param(lambda b: b["traces"][0].update(tags=["t"] * 21), id="too-many-tags"),
        pytest.param(lambda b: b.update(spans=b["spans"] * 1001), id="too-many-spans"),
        pytest.param(lambda b: b.update(traces=[], spans=[]), id="empty-batch"),
    ],
)
def test_invalid_batches_are_rejected_atomically(client: ApiClient, project_key, db: Session, mutate) -> None:
    _, key = project_key
    trace_id = uuid.uuid4()
    batch = {"traces": [trace(trace_id)], "spans": [span(trace_id)]}
    mutate(batch)
    response = client.post(INGEST, json=batch, headers=bearer(key))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert db.scalar(select(func.count()).select_from(Trace)) == 0  # nothing from the batch was stored


def test_ingestion_requires_a_valid_project_api_key(alice: ApiClient, client: ApiClient, project_key, db: Session) -> None:
    project, key = project_key
    trace_id, batch = full_trace()
    assert client.post(INGEST, json=batch).status_code == 401
    assert client.post(INGEST, json=batch, headers=bearer("tf_deadbeef_" + "x" * 43)).status_code == 401
    # A console session (with CSRF) cannot ingest either.
    assert alice.post(INGEST, json=batch).status_code == 401

    key_id = alice.get(f"/api/v1/projects/{project['id']}/api-keys").json()[0]["id"]
    alice.delete(f"/api/v1/projects/{project['id']}/api-keys/{key_id}")
    assert client.post(INGEST, json=batch, headers=bearer(key)).status_code == 401
    assert db.scalar(select(func.count()).select_from(Trace)) == 0


def test_projects_are_isolated_even_with_the_same_trace_id(
    alice: ApiClient, bob: ApiClient, client: ApiClient, project_key, db: Session
) -> None:
    alice_project, alice_key = project_key
    bob_project = bob.create_project("Bob agent", slug="bob")
    bob_key = bob.create_api_key(bob_project["id"])["key"]

    shared_id = uuid.uuid4()
    client.post(INGEST, json={"traces": [trace(shared_id, name="alice-trace")]}, headers=bearer(alice_key))
    client.post(INGEST, json={"traces": [trace(shared_id, name="bob-trace")]}, headers=bearer(bob_key))
    assert db.scalar(select(func.count()).select_from(Trace)) == 2  # bob did not overwrite alice

    assert alice.get(f"/api/v1/projects/{alice_project['id']}/traces/{shared_id}").json()["name"] == "alice-trace"
    assert bob.get(f"/api/v1/projects/{bob_project['id']}/traces/{shared_id}").json()["name"] == "bob-trace"
    for response in (
        bob.get(f"/api/v1/projects/{alice_project['id']}/traces"),
        bob.get(f"/api/v1/projects/{alice_project['id']}/traces/{shared_id}"),
    ):
        assert response.status_code == 404 and response.json()["error"]["code"] == "project_not_found"
    only_alice = uuid.uuid4()
    client.post(INGEST, json={"traces": [trace(only_alice)]}, headers=bearer(alice_key))
    missing = bob.get(f"/api/v1/projects/{bob_project['id']}/traces/{only_alice}")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "trace_not_found"


def test_trace_list_filters_and_paginates(alice: ApiClient, client: ApiClient, project_key) -> None:
    project, key = project_key
    ids = [uuid.uuid4() for _ in range(5)]
    traces = [
        trace(ids[i], name=f"run-{i}", started_at=iso(i * 1000), ended_at=iso(i * 1000 + 10),
              status="error" if i % 2 else "ok", tags=["nightly"] if i < 2 else [])
        for i in range(5)
    ]
    spans = [span(ids[1], status="error", started_at=iso(1000), ended_at=iso(1005))]
    assert client.post(INGEST, json={"traces": traces, "spans": spans}, headers=bearer(key)).status_code == 202
    url = f"/api/v1/projects/{project['id']}/traces"

    def names(**params: Any) -> list[str]:
        return [t["name"] for t in alice.get(url, params=params).json()["items"]]

    assert names() == ["run-4", "run-3", "run-2", "run-1", "run-0"]  # newest first
    assert names(status="error") == ["run-3", "run-1"]
    assert names(q="RUN-2") == ["run-2"]
    assert names(q=str(ids[0])) == ["run-0"]
    assert names(q="%") == []  # LIKE wildcards are matched literally
    assert names(tag="nightly") == ["run-1", "run-0"]
    assert names(has_errors=True) == ["run-1"]
    assert names(since=iso(2000), until=iso(4000)) == ["run-3", "run-2"]

    first = alice.get(url, params={"limit": 2}).json()
    second = alice.get(url, params={"limit": 2, "cursor": first["next_cursor"]}).json()
    third = alice.get(url, params={"limit": 2, "cursor": second["next_cursor"]}).json()
    pages = [[t["name"] for t in p["items"]] for p in (first, second, third)]
    assert pages == [["run-4", "run-3"], ["run-2", "run-1"], ["run-0"]] and third["next_cursor"] is None
    bad = alice.get(url, params={"cursor": "not-a-cursor"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_cursor"


def test_trace_reads_require_a_session(client: ApiClient, project_key) -> None:
    project, key = project_key
    assert client.get(f"/api/v1/projects/{project['id']}/traces", headers=bearer(key)).status_code == 401


def test_deleting_a_project_deletes_its_telemetry(alice: ApiClient, client: ApiClient, project_key, db: Session) -> None:
    project, key = project_key
    client.post(INGEST, json=full_trace()[1], headers=bearer(key))
    assert alice.delete(f"/api/v1/projects/{project['id']}").status_code == 204
    assert db.scalar(select(func.count()).select_from(Span)) == 0
    assert db.scalar(select(func.count()).select_from(Trace)) == 0


def test_database_rejects_spans_without_a_trace_in_the_same_project(client: ApiClient, project_key, db: Session) -> None:
    project, key = project_key
    trace_id = uuid.uuid4()
    client.post(INGEST, json={"traces": [trace(trace_id)]}, headers=bearer(key))
    other_project = uuid.uuid4()
    with pytest.raises(IntegrityError, match="fk_spans_project_id_trace_id_traces"):
        db.execute(
            text("INSERT INTO spans (project_id, id, trace_id, name, kind, started_at) "
                 "VALUES (:p, :i, :t, 'x', 'custom', now())"),
            {"p": other_project, "i": uuid.uuid4(), "t": trace_id},
        )
    db.rollback()


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [("kind", "'bogus'", "ck_spans_kind_valid"), ("status", "'done'", "ck_spans_status_valid"),
     ("prompt_tokens", "-5", "ck_spans_usage_non_negative"),
     ("ended_at", "now() - interval '1 day'", "ck_spans_ended_after_started")],
)
def test_database_enforces_span_constraints(client: ApiClient, project_key, db: Session, column, value, constraint) -> None:
    project, key = project_key
    trace_id, span_id = uuid.uuid4(), uuid.uuid4()
    client.post(INGEST, json={"traces": [trace(trace_id)], "spans": [span(trace_id, span_id)]}, headers=bearer(key))
    with pytest.raises(IntegrityError, match=constraint):
        db.execute(text(f"UPDATE spans SET {column} = {value} WHERE id = :i"), {"i": span_id})
    db.rollback()


def test_values_the_database_cannot_store_are_a_422_not_a_500(client, alice, caplog) -> None:
    """NaN in a JSON field, or NUL in text, used to be a 500, which SDKs retry and then drop the whole batch for."""
    project = alice.create_project("Unstorable", slug="unstorable")
    key = alice.create_api_key(project["id"])["key"]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    trace = '{"id":"11111111-1111-1111-1111-111111111111","name":"t","started_at":"2026-10-31T10:00:00Z","input":{"x":NaN}}'
    response = client.post("/api/v1/ingest/batch", content='{"traces":[' + trace + '],"spans":[]}', headers=headers)
    assert response.status_code == 422 and response.json()["error"]["code"] == "unstorable_value"
    assert "NaN" in response.json()["error"]["message"] and '"x"' not in response.text
    named = alice.post(f"/api/v1/projects/{project['id']}/datasets", json={"name": "bad\u0000name"})
    assert named.status_code == 422 and named.json()["error"]["code"] == "unstorable_value"
    assert "unhandled error" not in caplog.text
    # The connection is usable again afterwards.
    assert alice.get(f"/api/v1/projects/{project['id']}/datasets").json() == []
