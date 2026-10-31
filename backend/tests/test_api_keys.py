import json
import logging
import uuid

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.security import hash_api_key
from app.models import AuditLog, Project, ProjectApiKey, User
from tests.conftest import TEST_API_KEY_PEPPER, ApiClient

SELF = "/api/v1/api-keys/self"


def keys_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/api-keys"


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_create_returns_secret_once_and_stores_only_hmac(alice: ApiClient, db: Session) -> None:
    project = alice.create_project()
    response = alice.post(keys_url(project["id"]), json={"name": "ci"})
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    created = response.json()
    secret = created["key"]
    assert secret.startswith(f"tf_{created['prefix']}_") and len(created["prefix"]) == 8
    assert created["project_id"] == project["id"] and created["revoked_at"] is None
    assert "key_hash" not in created

    stored = db.scalar(select(ProjectApiKey))
    assert stored is not None
    assert stored.key_hash == hash_api_key(secret, TEST_API_KEY_PEPPER)
    assert secret not in stored.key_hash

    # The secret can never be retrieved again.
    listing = alice.get(keys_url(project["id"]))
    assert listing.status_code == 200
    assert secret not in listing.text
    assert [set(k) for k in listing.json()] == [
        {"id", "project_id", "name", "prefix", "created_at", "last_used_at", "revoked_at"}
    ]


def test_key_authenticates_and_records_last_use(alice: ApiClient, client: ApiClient, db: Session) -> None:
    project = alice.create_project(slug="checkout")
    created = alice.create_api_key(project["id"])
    response = client.get(SELF, headers=bearer(created["key"]))
    assert response.status_code == 200
    assert response.json() == {
        "project_id": project["id"],
        "project_slug": "checkout",
        "key_id": created["id"],
        "key_prefix": created["prefix"],
    }
    assert db.scalar(select(ProjectApiKey.last_used_at)) is not None


@pytest.mark.parametrize(
    "header",
    [
        None,
        "Bearer not-a-traceforge-key",
        "Bearer tf_deadbeef_" + "x" * 43,  # well-formed, unknown prefix
        "Basic dXNlcjpwYXNz",
    ],
)
def test_invalid_key_headers_are_rejected(client: ApiClient, header: str | None) -> None:
    response = client.get(SELF, headers={"Authorization": header} if header else {})
    assert response.status_code == 401
    assert response.json()["error"]["code"] in {"api_key_required", "invalid_api_key"}


def test_right_prefix_wrong_secret_is_rejected(alice: ApiClient, client: ApiClient) -> None:
    created = alice.create_api_key(alice.create_project()["id"])
    tampered = created["key"][:-4] + ("AAAA" if not created["key"].endswith("AAAA") else "BBBB")
    response = client.get(SELF, headers=bearer(tampered))
    assert response.status_code == 401
    assert tampered not in response.text


def test_revoked_key_is_rejected_immediately(alice: ApiClient, client: ApiClient, db: Session) -> None:
    project = alice.create_project()
    created = alice.create_api_key(project["id"])
    assert client.get(SELF, headers=bearer(created["key"])).status_code == 200

    assert alice.delete(f"{keys_url(project['id'])}/{created['id']}").status_code == 204
    response = client.get(SELF, headers=bearer(created["key"]))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"

    listed = alice.get(keys_url(project["id"])).json()
    assert listed[0]["revoked_at"] is not None

    # Revocation is idempotent and audited once.
    assert alice.delete(f"{keys_url(project['id'])}/{created['id']}").status_code == 204
    revocations = db.scalars(select(AuditLog).where(AuditLog.action == "api_key.revoked")).all()
    assert len(revocations) == 1


def test_revoking_one_key_leaves_others_working(alice: ApiClient, client: ApiClient) -> None:
    project = alice.create_project()
    first = alice.create_api_key(project["id"], "first")
    second = alice.create_api_key(project["id"], "second")
    alice.delete(f"{keys_url(project['id'])}/{first['id']}")
    assert client.get(SELF, headers=bearer(second["key"])).status_code == 200


def test_key_is_scoped_to_its_own_project(alice: ApiClient, bob: ApiClient, client: ApiClient) -> None:
    alice_project = alice.create_project("Alice project", slug="alice")
    bob_project = bob.create_project("Bob project", slug="bob")
    alice_key = alice.create_api_key(alice_project["id"])
    bob_key = bob.create_api_key(bob_project["id"])
    assert client.get(SELF, headers=bearer(alice_key["key"])).json()["project_id"] == alice_project["id"]
    assert client.get(SELF, headers=bearer(bob_key["key"])).json()["project_id"] == bob_project["id"]


def test_api_key_cannot_authenticate_console_routes(alice: ApiClient, client: ApiClient) -> None:
    project = alice.create_project()
    key = alice.create_api_key(project["id"])["key"]
    for response in (
        client.get("/api/v1/projects", headers=bearer(key)),
        client.get(keys_url(project["id"]), headers=bearer(key)),
        client.get("/api/v1/auth/me", headers=bearer(key)),
    ):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "not_authenticated"


def test_other_user_cannot_manage_keys(alice: ApiClient, bob: ApiClient, db: Session) -> None:
    project = alice.create_project()
    key = alice.create_api_key(project["id"])
    for response in (
        bob.get(keys_url(project["id"])),
        bob.post(keys_url(project["id"]), json={"name": "stolen"}),
        bob.delete(f"{keys_url(project['id'])}/{key['id']}"),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "project_not_found"
    stored = db.scalar(select(ProjectApiKey))
    assert stored is not None and stored.revoked_at is None
    assert db.scalar(select(ProjectApiKey).where(ProjectApiKey.name == "stolen")) is None


def test_key_cannot_be_revoked_through_another_project(alice: ApiClient, bob: ApiClient, db: Session) -> None:
    alice_project = alice.create_project("A", slug="a")
    alice_key = alice.create_api_key(alice_project["id"])
    other_alice_project = alice.create_project("A2", slug="a2")
    bob_project = bob.create_project("B", slug="b")

    # Owner of the path project, but the key belongs to a different project.
    wrong_path = alice.delete(f"{keys_url(other_alice_project['id'])}/{alice_key['id']}")
    assert wrong_path.status_code == 404
    assert wrong_path.json()["error"]["code"] == "api_key_not_found"
    # Another user's own project in the path does not grant access to Alice's key either.
    assert bob.delete(f"{keys_url(bob_project['id'])}/{alice_key['id']}").status_code == 404

    assert db.scalar(select(ProjectApiKey.revoked_at)) is None


def test_key_writes_require_csrf(alice: ApiClient) -> None:
    project = alice.create_project()
    key = alice.create_api_key(project["id"])
    alice.headers.pop("X-CSRF-Token")
    assert alice.post(keys_url(project["id"]), json={"name": "x"}).status_code == 403
    assert alice.delete(f"{keys_url(project['id'])}/{key['id']}").status_code == 403


def test_deleting_project_cascades_keys_and_invalidates_them(alice: ApiClient, client: ApiClient, db: Session) -> None:
    project = alice.create_project()
    key = alice.create_api_key(project["id"])["key"]
    assert alice.delete(f"/api/v1/projects/{project['id']}").status_code == 204
    assert db.scalar(select(ProjectApiKey)) is None
    assert client.get(SELF, headers=bearer(key)).status_code == 401


def test_key_of_inactive_owner_is_rejected(alice: ApiClient, client: ApiClient, db: Session) -> None:
    key = alice.create_api_key(alice.create_project()["id"])["key"]
    db.execute(update(User).values(is_active=False))
    db.commit()
    assert client.get(SELF, headers=bearer(key)).status_code == 401


def test_deleting_creator_keeps_key_with_null_creator(alice: ApiClient, db: Session) -> None:
    project = alice.create_project()
    alice.create_api_key(project["id"])
    # Re-own the project by another user, then delete the creator: created_by is SET NULL.
    other = User(email="owner2@example.com", password_hash="x", display_name="O")
    db.add(other)
    db.flush()
    db.execute(update(Project).values(owner_id=other.id))
    db.execute(User.__table__.delete().where(User.email == "alice@example.com"))
    db.commit()
    stored = db.scalar(select(ProjectApiKey))
    assert stored is not None and stored.created_by is None


def test_audit_records_for_key_lifecycle_contain_no_credentials(alice: ApiClient, db: Session) -> None:
    project = alice.create_project()
    created = alice.create_api_key(project["id"], "deploy")
    alice.delete(f"{keys_url(project['id'])}/{created['id']}")
    alice.delete(f"/api/v1/projects/{project['id']}")

    entries = db.scalars(select(AuditLog).order_by(AuditLog.created_at)).all()
    assert [e.action for e in entries] == ["api_key.created", "api_key.revoked", "project.deleted"]
    key_entries = entries[:2]
    for entry in key_entries:
        assert entry.target_type == "api_key" and entry.target_id == created["id"]
        assert entry.details == {"name": "deploy", "prefix": created["prefix"]}

    stored_hash = hash_api_key(created["key"], TEST_API_KEY_PEPPER)
    serialized = json.dumps([[e.action, e.target_id, e.details] for e in entries])
    secret_part = created["key"].split("_", 2)[2]
    for forbidden in (created["key"], secret_part, stored_hash, alice.cookies["tf_session"]):
        assert forbidden not in serialized


def test_secrets_never_reach_log_output(alice: ApiClient, client: ApiClient, caplog: pytest.LogCaptureFixture) -> None:
    from app.core.logging import JsonFormatter

    caplog.set_level(logging.DEBUG)
    project = alice.create_project()
    created = alice.create_api_key(project["id"])
    client.get(SELF, headers=bearer(created["key"]))
    client.get(SELF, headers=bearer(created["key"][:-2] + "zz"))

    formatter = JsonFormatter()
    output = "\n".join(formatter.format(record) for record in caplog.records)
    secret_part = created["key"].split("_", 2)[2]
    assert secret_part not in output
    assert alice.cookies["tf_session"] not in output


def test_unknown_key_id_in_own_project_is_404(alice: ApiClient) -> None:
    project = alice.create_project()
    response = alice.delete(f"{keys_url(project['id'])}/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "api_key_not_found"
