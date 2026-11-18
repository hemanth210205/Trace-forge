"""Cross-project isolation sweep over every route in the OpenAPI schema (new routes are covered automatically).

Alice owns a project with one of every resource. Bob, an authenticated user with his own project and API key,
calls every project-scoped route with Alice's real ids — both inside Alice's project and inside his own project
(the subtler IDOR: a valid parent with a foreign child id). Every call must be a 404 that leaks nothing, and
Alice's data must be unchanged afterwards. Request bodies are valid so that the ownership check, not body
validation, is what answers.
"""

import re
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Dataset, DatasetCase, Diagnosis, EvaluationRun, Experiment, ExperimentDecision, Project, ProjectApiKey, Trace
from tests.conftest import ApiClient
from tests.test_evaluation_runs import Env
from tests.test_experiments import BASELINE, CANDIDATE, create, env as experiment_env  # noqa: F401  (fixture)

BODIES: dict[tuple[str, str], Any] = {
    ("patch", "/projects/{project_id}"): {"name": "stolen"},
    ("post", "/projects/{project_id}/api-keys"): {"name": "stolen"},
    ("post", "/projects/{project_id}/datasets"): {"name": "stolen"},
    ("patch", "/projects/{project_id}/datasets/{dataset_id}"): {"name": "stolen"},
    ("post", "/projects/{project_id}/datasets/{dataset_id}/cases"): {"input": {"q": "x"}},
    ("patch", "/projects/{project_id}/datasets/{dataset_id}/cases/{case_id}"): {"input": {"q": "x"}},
    ("post", "/projects/{project_id}/datasets/{dataset_id}/import"): {"format": "json", "content": "[]"},
    ("post", "/projects/{project_id}/datasets/{dataset_id}/versions"): {},
    ("post", "/projects/{project_id}/experiments/{experiment_id}/decisions"): {"decision": "approve", "rationale": "stolen"},
    ("post", "/sdk/eval-runs/{run_id}/results"): lambda ids: {"results": [{"case_id": ids["case_id"], "output": {"answer": "x"}}]},
    ("post", "/projects/{project_id}/eval-runs"): lambda ids: {"dataset_version_id": ids["version_id"], "name": "x", "evaluators": [{"type": "exact_match"}]},
    ("post", "/projects/{project_id}/experiments"): lambda ids: {"source_run_id": ids["run_id"], "name": "x", "baseline_config": BASELINE,
                                                                 "candidate_config": CANDIDATE, "hypothesis_id": ids["hypothesis_id"]},
}
# Routes whose project comes from the body or the key, not the path; checked separately below.
NOT_ID_SCOPED = {"/health", "/auth/register", "/auth/login", "/auth/logout", "/auth/me", "/projects", "/api-keys/self",
                 "/ingest/batch", "/sdk/eval-runs",
                 # Sign-in providers: no project involved (tests/test_oauth.py).
                 "/auth/providers", "/auth/registration", "/auth/oauth/{provider}/start",
                 "/auth/oauth/{provider}/callback"}


@pytest.fixture
def world(experiment_env: Env, bob: ApiClient, db: Session) -> dict[str, Any]:  # noqa: F811
    env = experiment_env
    exp = create(env).json()
    diagnosis = env.alice.post(f"{env.base}/eval-runs/{env.source['id']}/diagnoses").json()  # type: ignore[attr-defined]
    detail = env.alice.get(f"{env.base}/diagnoses/{diagnosis['id']}").json()
    hypothesis = next(h for c in detail["cases"] for h in c["hypotheses"])
    bob_project = bob.create_project("Bob agent", slug="bob-agent")
    ids = {
        "project_id": env.project["id"], "dataset_id": env.dataset["id"], "version": env.version["version"], "version_id": env.version["id"],
        "case_id": env.alice.get(f"{env.base}/datasets/{env.dataset['id']}/cases").json()[0]["id"],
        "run_id": env.source["id"], "diagnosis_id": diagnosis["id"], "hypothesis_id": hypothesis["id"],  # type: ignore[attr-defined]
        "experiment_id": exp["id"], "trace_id": env.alice.get(f"{env.base}/traces").json()["items"][0]["id"],
        "key_id": env.alice.get(f"{env.base}/api-keys").json()[0]["id"],
    }
    return {"env": env, "ids": ids, "bob_project": bob_project, "bob_key": bob.create_api_key(bob_project["id"])["key"],
            "snapshot": snapshot(db, env.project["id"])}


def snapshot(db: Session, project_id: str) -> dict[str, Any]:
    db.expire_all()
    count = lambda model: db.scalar(select(func.count()).select_from(model).where(model.project_id == project_id))  # noqa: E731
    project = db.get(Project, project_id)
    assert project is not None
    return {
        "project": (project.name, project.slug), "datasets": db.scalars(select(Dataset.name).where(Dataset.project_id == project_id)).all(),
        "cases": db.scalar(select(func.count()).select_from(DatasetCase).join(Dataset).where(Dataset.project_id == project_id)),
        "runs": sorted(db.execute(select(EvaluationRun.id, EvaluationRun.status).where(EvaluationRun.project_id == project_id)).all()),
        "experiments": sorted(db.execute(select(Experiment.id, Experiment.status).where(Experiment.project_id == project_id)).all()),
        "decisions": db.scalar(select(func.count()).select_from(ExperimentDecision)),
        "diagnoses": count(Diagnosis), "traces": sorted(db.scalars(select(Trace.name).where(Trace.project_id == project_id)).all()),
        "keys": sorted(db.execute(select(ProjectApiKey.id, ProjectApiKey.revoked_at).where(ProjectApiKey.project_id == project_id)).all()),
    }


def operations(app: FastAPI) -> list[tuple[str, str]]:
    return [(method, path.removeprefix("/api/v1")) for path, ops in app.openapi()["paths"].items() if path.startswith("/api/v1")
            for method in ops if path.removeprefix("/api/v1") not in NOT_ID_SCOPED]


def call(client: ApiClient, method: str, template: str, ids: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
    path = "/api/v1" + re.sub(r"\{(\w+)\}", lambda m: str(ids[m.group(1)]), template)
    body = BODIES.get((method, template))
    body = body(ids) if callable(body) else body
    return client.request(method.upper(), path, json=body, headers=headers or {})


def assert_hidden(response: Any, alice_ids: dict[str, Any], route: str) -> None:
    assert response.status_code == 404, f"{route}: {response.status_code} {response.text[:200]}"
    text = response.text
    leaked = [k for k, v in alice_ids.items() if k != "version" and str(v) in text]
    assert not leaked, f"{route} leaked {leaked}"


def test_every_id_scoped_route_hides_other_projects(world: dict[str, Any], bob: ApiClient, app: FastAPI, db: Session) -> None:
    alice_ids, routes = world["ids"], operations(app)
    assert len(routes) >= 40, routes  # the sweep really covers the API
    console = [(m, p) for m, p in routes if not p.startswith("/sdk/")]
    sdk = [(m, p) for m, p in routes if p.startswith("/sdk/")]
    for method, template in console:
        # Alice's ids under Alice's project, then under Bob's own project.
        assert_hidden(call(bob, method, template, alice_ids), alice_ids, f"{method} {template} (foreign project)")
        if template != "/projects/{project_id}" and "{" in template.removeprefix("/projects/{project_id}"):
            assert_hidden(call(bob, method, template, {**alice_ids, "project_id": world["bob_project"]["id"]}), alice_ids,
                          f"{method} {template} (own project, foreign child)")
    for method, template in sdk:
        assert_hidden(call(bob, method, template, alice_ids, {"Authorization": f"Bearer {world['bob_key']}"}), alice_ids,
                      f"{method} {template} (foreign key)")
    assert snapshot(db, alice_ids["project_id"]) == world["snapshot"]


def test_body_references_to_other_projects_are_rejected(world: dict[str, Any], bob: ApiClient, db: Session) -> None:
    ids, own = world["ids"], world["bob_project"]["id"]
    base = f"/api/v1/projects/{own}"
    run = bob.post(f"{base}/eval-runs", json={"dataset_version_id": world["env"].version["id"], "name": "x", "evaluators": [{"type": "exact_match"}]})
    assert run.status_code == 404
    exp = bob.post(f"{base}/experiments", json={"source_run_id": ids["run_id"], "name": "x", "baseline_config": BASELINE, "candidate_config": CANDIDATE})
    assert exp.status_code == 404
    # The SDK can only name datasets of its own project.
    sdk = bob.post("/api/v1/sdk/eval-runs", json={"dataset": "Support QA", "name": "x", "evaluators": [{"type": "exact_match"}]},
                   headers={"Authorization": f"Bearer {world['bob_key']}"})
    assert sdk.status_code == 404
    assert snapshot(db, ids["project_id"]) == world["snapshot"]


def test_telemetry_with_colliding_ids_stays_in_the_senders_project(world: dict[str, Any], bob: ApiClient, db: Session) -> None:
    ids = world["ids"]
    batch = {"traces": [{"id": ids["trace_id"], "name": "overwrite attempt", "started_at": "2026-10-31T10:00:00Z"}], "spans": []}
    assert bob.post("/api/v1/ingest/batch", json=batch, headers={"Authorization": f"Bearer {world['bob_key']}"}).status_code == 202
    assert snapshot(db, ids["project_id"]) == world["snapshot"]
    assert db.scalar(select(Trace.name).where(Trace.project_id == world["bob_project"]["id"], Trace.id == ids["trace_id"])) == "overwrite attempt"
