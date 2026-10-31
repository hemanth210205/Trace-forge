import uuid

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AuditLog, Project, User
from tests.conftest import ApiClient

PROJECTS = "/api/v1/projects"


def test_create_project_derives_slug_and_defaults(alice: ApiClient) -> None:
    project = alice.create_project("Support Agent — EU!")
    assert project["slug"] == "support-agent-eu"
    assert project["environment"] == "development"
    assert project["description"] == ""
    assert project["settings"] == {}
    assert project["is_demo"] is False
    assert "owner_id" not in project


def test_create_project_with_all_fields(alice: ApiClient) -> None:
    project = alice.create_project(
        "Billing",
        slug="billing-v2",
        description="Handles invoices",
        agent_version="1.4.0",
        environment="production",
        settings={"sampling": 0.5, "tags": ["billing"]},
    )
    assert project["slug"] == "billing-v2"
    assert project["settings"] == {"sampling": 0.5, "tags": ["billing"]}
    assert project["environment"] == "production"


@pytest.mark.parametrize(
    "fields",
    [
        {"slug": "Has Spaces"},
        {"slug": "-leading-hyphen"},
        {"environment": "qa"},
        {"is_demo": True},
        {"owner_id": str(uuid.uuid4())},
    ],
)
def test_create_project_rejects_invalid_or_server_owned_fields(alice: ApiClient, fields: dict) -> None:
    response = alice.post(PROJECTS, json={"name": "P", "agent_name": "a", **fields})
    assert response.status_code == 422


def test_project_writes_require_csrf(alice: ApiClient) -> None:
    project = alice.create_project()
    csrf = alice.headers.pop("X-CSRF-Token")
    assert alice.post(PROJECTS, json={"name": "P", "agent_name": "a"}).status_code == 403
    assert alice.patch(f"{PROJECTS}/{project['id']}", json={"name": "New"}).status_code == 403
    assert alice.delete(f"{PROJECTS}/{project['id']}").status_code == 403
    assert alice.get(f"{PROJECTS}/{project['id']}").status_code == 200  # reads do not need CSRF
    alice.headers["X-CSRF-Token"] = csrf
    assert alice.get(f"{PROJECTS}/{project['id']}").json()["name"] == "Support Agent"


def test_projects_require_authentication(client: ApiClient) -> None:
    assert client.get(PROJECTS).status_code == 401
    assert client.post(PROJECTS, json={"name": "P", "agent_name": "a"}).status_code == 401


def test_slug_unique_per_owner_but_not_globally(alice: ApiClient, bob: ApiClient) -> None:
    alice.create_project("Agent", slug="agent")
    duplicate = alice.post(PROJECTS, json={"name": "Other", "agent_name": "a", "slug": "agent"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "slug_taken"
    assert bob.create_project("Agent", slug="agent")["slug"] == "agent"


def test_list_returns_only_own_projects(alice: ApiClient, bob: ApiClient) -> None:
    alice.create_project("A1")
    alice.create_project("A2")
    bob.create_project("B1")
    assert sorted(p["name"] for p in alice.get(PROJECTS).json()) == ["A1", "A2"]
    assert [p["name"] for p in bob.get(PROJECTS).json()] == ["B1"]


def test_update_project(alice: ApiClient) -> None:
    project = alice.create_project()
    response = alice.patch(
        f"{PROJECTS}/{project['id']}", json={"name": "Renamed", "agent_version": None, "settings": {"k": 1}}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed" and body["agent_version"] is None and body["settings"] == {"k": 1}
    assert body["slug"] == project["slug"]  # unchanged fields stay put
    assert body["updated_at"] > project["updated_at"]


def test_update_rejects_null_for_required_fields(alice: ApiClient) -> None:
    project = alice.create_project()
    assert alice.patch(f"{PROJECTS}/{project['id']}", json={"name": None}).status_code == 422


def test_update_slug_conflict(alice: ApiClient) -> None:
    alice.create_project("One", slug="one")
    two = alice.create_project("Two", slug="two")
    response = alice.patch(f"{PROJECTS}/{two['id']}", json={"slug": "one"})
    assert response.status_code == 409


def test_cross_user_access_returns_404(alice: ApiClient, bob: ApiClient, db: Session) -> None:
    project = alice.create_project()
    url = f"{PROJECTS}/{project['id']}"
    responses = [
        bob.get(url),
        bob.patch(url, json={"name": "Hijacked"}),
        bob.delete(url),
    ]
    for response in responses:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "project_not_found"
    # Same response as a project that never existed.
    assert bob.get(f"{PROJECTS}/{uuid.uuid4()}").json()["error"]["code"] == "project_not_found"

    stored = db.get(Project, uuid.UUID(project["id"]))
    assert stored is not None and stored.name == "Support Agent"


def test_delete_project_writes_audit_record_that_survives(alice: ApiClient, db: Session) -> None:
    project = alice.create_project("Doomed", slug="doomed")
    assert alice.delete(f"{PROJECTS}/{project['id']}").status_code == 204
    assert alice.get(f"{PROJECTS}/{project['id']}").status_code == 404

    entry = db.scalar(select(AuditLog).where(AuditLog.action == "project.deleted"))
    assert entry is not None
    assert entry.project_id is None  # FK SET NULL fired on delete
    assert entry.target_type == "project" and entry.target_id == project["id"]
    assert entry.details == {"name": "Doomed", "slug": "doomed"}
    alice_id = db.scalar(select(User.id).where(User.email == "alice@example.com"))
    assert entry.actor_user_id == alice_id


def test_deleting_user_cascades_projects(alice: ApiClient, db: Session) -> None:
    alice.create_project()
    db.execute(delete(User).where(User.email == "alice@example.com"))
    db.commit()
    assert db.scalar(select(func.count()).select_from(Project)) == 0


def test_database_enforces_slug_format(alice: ApiClient, db: Session) -> None:
    owner_id = db.scalar(select(User.id))
    db.add(Project(owner_id=owner_id, name="X", slug="Bad Slug", agent_name="a"))
    with pytest.raises(IntegrityError, match="ck_projects_slug_format"):
        db.flush()


def test_database_enforces_owner_slug_uniqueness(alice: ApiClient, db: Session) -> None:
    owner_id = db.scalar(select(User.id))
    db.add_all([Project(owner_id=owner_id, name="X", slug="same", agent_name="a") for _ in range(2)])
    with pytest.raises(IntegrityError, match="uq_projects_owner_id_slug"):
        db.flush()


def test_database_enforces_owner_foreign_key(db: Session) -> None:
    db.add(Project(owner_id=uuid.uuid4(), name="X", slug="x", agent_name="a"))
    with pytest.raises(IntegrityError, match="fk_projects_owner_id_users"):
        db.flush()
