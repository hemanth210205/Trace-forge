"""Datasets, draft cases, import/export and immutable versions (real PostgreSQL)."""

import json
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import AuditLog, DatasetVersionCase
from tests.conftest import ApiClient

CASES = [
    {"key": "shipping", "input": {"question": "Where is my order A100?"}, "expected_output": "Your order is shipped with ParcelCo.",
     "expected_sources": ["kb/shipping.md"], "metadata": {"topic": "orders"}},
    {"key": "refunds", "input": {"question": "How do refunds work?"}, "expected_output": "refund", "expected_sources": ["kb/returns.md"]},
]


@pytest.fixture
def project(alice: ApiClient) -> dict[str, Any]:
    return alice.create_project("Agent", slug="agent")


def base(project: dict[str, Any]) -> str:
    return f"/api/v1/projects/{project['id']}/datasets"


def create_dataset(client: ApiClient, project: dict[str, Any], name: str = "Support QA", cases: list | None = None) -> dict[str, Any]:
    response = client.post(base(project), json={"name": name, "description": "Golden questions", "metadata": {"owner": "qa"}})
    assert response.status_code == 201, response.text
    dataset = response.json()
    for case in cases or []:
        assert client.post(f"{base(project)}/{dataset['id']}/cases", json=case).status_code == 201
    return dataset


def test_dataset_crud_and_unique_name(alice: ApiClient, project) -> None:
    dataset = create_dataset(alice, project)
    assert dataset["draft_case_count"] == 0 and dataset["latest_version"] is None
    assert alice.post(base(project), json={"name": "Support QA"}).status_code == 409
    renamed = alice.patch(f"{base(project)}/{dataset['id']}", json={"name": "Support QA v2", "description": "x"}).json()
    assert renamed["name"] == "Support QA v2" and renamed["metadata"] == {"owner": "qa"}
    assert [d["name"] for d in alice.get(base(project)).json()] == ["Support QA v2"]
    assert alice.delete(f"{base(project)}/{dataset['id']}").status_code == 204
    assert alice.get(f"{base(project)}/{dataset['id']}").status_code == 404


def test_draft_case_editing(alice: ApiClient, project) -> None:
    dataset = create_dataset(alice, project, cases=CASES)
    url = f"{base(project)}/{dataset['id']}/cases"
    cases = alice.get(url).json()
    assert [c["key"] for c in cases] == ["shipping", "refunds"] and cases[0]["expected_sources"] == ["kb/shipping.md"]
    generated = alice.post(url, json={"input": "hello"}).json()
    assert generated["key"] == "case-3" and generated["position"] == 3
    assert alice.post(url, json={"key": "shipping", "input": "dup"}).status_code == 409
    assert alice.post(url, json={"input": None}).status_code == 422
    assert alice.post(url, json={"key": "has space", "input": "x"}).status_code == 422

    edited = alice.patch(f"{url}/{generated['id']}", json={"expected_output": {"greeting": True}, "key": "greeting"}).json()
    assert edited["expected_output"] == {"greeting": True} and edited["key"] == "greeting"
    assert alice.patch(f"{url}/{generated['id']}", json={"expected_output": None}).json()["expected_output"] is None
    assert alice.patch(f"{url}/{generated['id']}", json={"input": None}).status_code == 422
    assert alice.delete(f"{url}/{generated['id']}").status_code == 204
    assert len(alice.get(url).json()) == 2


def test_publishing_creates_immutable_numbered_versions(alice: ApiClient, project, db: Session) -> None:
    dataset = create_dataset(alice, project, cases=CASES)
    url = f"{base(project)}/{dataset['id']}"
    v1 = alice.post(f"{url}/versions", json={"notes": "first"})
    assert v1.status_code == 201 and v1.json()["version"] == 1 and v1.json()["case_count"] == 2
    assert alice.post(f"{url}/versions", json={}).json()["error"]["code"] == "no_changes"
    assert alice.get(url).json()["draft_changed_since_latest"] is False

    # Editing the draft never changes version 1; publishing again makes version 2.
    shipping = alice.get(f"{url}/cases").json()[0]
    alice.patch(f"{url}/cases/{shipping['id']}", json={"expected_output": "changed"})
    assert alice.get(url).json()["draft_changed_since_latest"] is True
    assert alice.get(f"{url}/versions/1").json()["cases"][0]["expected_output"] == "Your order is shipped with ParcelCo."
    v2 = alice.post(f"{url}/versions", json={"notes": "edited"}).json()
    assert v2["version"] == 2 and v2["content_hash"] != v1.json()["content_hash"]
    assert [v["version"] for v in alice.get(f"{url}/versions").json()] == [2, 1]
    assert alice.get(f"{url}/versions/2").json()["cases"][0]["expected_output"] == "changed"
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "dataset.version_published")) == 2

    # The database itself refuses to modify a published version.
    version_case_id = db.scalar(select(DatasetVersionCase.id).limit(1))
    with pytest.raises(DBAPIError, match="dataset_version_cases rows are immutable"):
        db.execute(text("UPDATE dataset_version_cases SET expected_output = '\"tampered\"' WHERE id = :i"), {"i": version_case_id})
    db.rollback()
    with pytest.raises(DBAPIError, match="dataset_versions rows are immutable"):
        db.execute(text("UPDATE dataset_versions SET notes = 'x'"))
    db.rollback()


def test_empty_draft_cannot_be_published(alice: ApiClient, project) -> None:
    dataset = create_dataset(alice, project)
    response = alice.post(f"{base(project)}/{dataset['id']}/versions", json={})
    assert response.status_code == 422 and response.json()["error"]["code"] == "empty_draft"


def test_jsonl_and_json_import_and_round_trip_export(alice: ApiClient, project) -> None:
    dataset = create_dataset(alice, project)
    url = f"{base(project)}/{dataset['id']}"
    jsonl = "\n".join(json.dumps(c) for c in CASES) + "\n\n"
    assert alice.post(f"{url}/import", json={"format": "jsonl", "content": jsonl}).json() == {"imported": 2, "draft_case_count": 2}
    exported = alice.get(f"{url}/export", params={"format": "jsonl"})
    assert exported.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment" in exported.headers["content-disposition"]
    lines = [json.loads(line) for line in exported.text.splitlines()]
    assert [line["key"] for line in lines] == ["shipping", "refunds"] and lines[0]["metadata"] == {"topic": "orders"}

    # Re-import the export into a second dataset (JSON format) and get identical cases.
    other = create_dataset(alice, project, name="Copy")
    as_json = json.dumps({"cases": lines})
    assert alice.post(f"{base(project)}/{other['id']}/import", json={"format": "json", "content": as_json}).status_code == 200
    assert alice.get(f"{base(project)}/{other['id']}/export").text == exported.text

    # Replace mode swaps the whole draft; append refuses clashing keys.
    clash = alice.post(f"{url}/import", json={"format": "jsonl", "content": json.dumps(CASES[0])})
    assert clash.status_code == 422 and clash.json()["error"]["details"][0]["at"] == "key shipping"
    alice.post(f"{url}/import", json={"format": "jsonl", "content": json.dumps({"key": "only", "input": 1}), "mode": "replace"})
    assert [c["key"] for c in alice.get(f"{url}/cases").json()] == ["only"]

    alice.post(f"{url}/versions", json={})
    assert json.loads(alice.get(f"{url}/export", params={"version": 1, "format": "json"}).text)["cases"][0]["key"] == "only"
    assert alice.get(f"{url}/export", params={"version": 9}).status_code == 404


def test_invalid_imports_report_every_problem_and_import_nothing(alice: ApiClient, project) -> None:
    dataset = create_dataset(alice, project)
    url = f"{base(project)}/{dataset['id']}"
    content = "\n".join([
        json.dumps({"key": "ok", "input": "x"}),
        "{not json",
        json.dumps({"key": "ok", "input": "y"}),
        json.dumps({"input": None}),
        json.dumps(["not", "an", "object"]),
        json.dumps({"input": "x", "expected_sources": "kb/a.md"}),
        json.dumps({"input": "x", "surprise": 1}),
    ])
    response = alice.post(f"{url}/import", json={"format": "jsonl", "content": content})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_import" and "nothing was imported" in error["message"]
    where = [d["at"] for d in error["details"]]
    assert where == ["line 2", "line 3", "line 4", "line 5", "line 6", "line 7"]
    assert "Duplicate key 'ok'" in error["details"][1]["message"]
    assert alice.get(f"{url}/cases").json() == []
    bad_json = alice.post(f"{url}/import", json={"format": "json", "content": '{"cases": 5}'})
    assert bad_json.status_code == 422


def test_cross_project_and_cross_user_isolation(alice: ApiClient, bob: ApiClient, project) -> None:
    dataset = create_dataset(alice, project, cases=CASES)
    url = f"{base(project)}/{dataset['id']}"
    alice.post(f"{url}/versions", json={})
    case_id = alice.get(f"{url}/cases").json()[0]["id"]
    for response in (
        bob.get(base(project)), bob.get(url), bob.get(f"{url}/cases"), bob.get(f"{url}/versions/1"),
        bob.patch(url, json={"name": "x"}), bob.delete(url), bob.post(f"{url}/versions", json={}),
        bob.delete(f"{url}/cases/{case_id}"), bob.get(f"{url}/export"),
    ):
        assert response.status_code == 404
    # A dataset id from another project of the same user is not reachable through the wrong project.
    other_project = alice.create_project("Other", slug="other")
    assert alice.get(f"/api/v1/projects/{other_project['id']}/datasets/{dataset['id']}").json()["error"]["code"] == "dataset_not_found"
    assert alice.get(f"{url}/cases").status_code == 200


def test_dataset_writes_require_csrf(alice: ApiClient, project) -> None:
    dataset = create_dataset(alice, project)
    alice.headers.pop("X-CSRF-Token")
    url = f"{base(project)}/{dataset['id']}"
    for response in (alice.post(base(project), json={"name": "x"}), alice.post(f"{url}/cases", json={"input": 1}),
                     alice.post(f"{url}/versions", json={}), alice.delete(url)):
        assert response.status_code == 403
