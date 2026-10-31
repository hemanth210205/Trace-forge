from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import sha256_hex
from app.db.base import utcnow
from app.models import User, UserSession
from tests.conftest import PASSWORD, ApiClient

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"


def test_register_normalizes_email_and_starts_session(client: ApiClient, db: Session) -> None:
    response = client.post(REGISTER, json={"email": "  Ada.Lovelace@Example.COM ", "password": PASSWORD, "display_name": " Ada "})
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "ada.lovelace@example.com"
    assert body["user"]["display_name"] == "Ada"
    assert set(body["user"]) == {"id", "email", "display_name", "created_at"}
    assert body["csrf_token"]
    assert response.headers["cache-control"] == "no-store"

    session_cookie = next(c for c in client.cookies.jar if c.name == "tf_session")
    assert session_cookie.has_nonstandard_attr("HttpOnly")
    assert client.cookies["tf_csrf"] == body["csrf_token"]

    user = db.scalar(select(User))
    assert user is not None and user.email == "ada.lovelace@example.com"
    assert user.password_hash.startswith("$argon2id$") and PASSWORD not in user.password_hash


def test_session_token_is_stored_only_as_sha256(client: ApiClient, db: Session) -> None:
    client.register("ada@example.com")
    token = client.cookies["tf_session"]
    stored = db.scalars(select(UserSession.token_hash)).all()
    assert stored == [sha256_hex(token)]
    assert token not in stored


def test_duplicate_email_is_rejected_case_insensitively(client: ApiClient, make_client) -> None:
    client.register("ada@example.com")
    other = make_client()
    response = other.post(REGISTER, json={"email": "ADA@example.com", "password": PASSWORD, "display_name": "Imposter"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": PASSWORD, "display_name": "A"},
        {"email": "ada@example.com", "password": "Zq9!pw", "display_name": "A"},
        {"email": "ada@example.com", "password": PASSWORD, "display_name": "   "},
        {"email": "ada@example.com", "password": PASSWORD, "display_name": "A", "is_active": False},
    ],
)
def test_register_validation_errors(client: ApiClient, payload: dict) -> None:
    response = client.post(REGISTER, json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert payload["password"] not in response.text


def test_login_normalizes_email(client: ApiClient, make_client) -> None:
    client.register("ada@example.com")
    fresh = make_client()
    response = fresh.post(LOGIN, json={"email": "  ADA@Example.com", "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "ada@example.com"
    fresh.remember_auth(response.json())
    assert fresh.get(ME).status_code == 200


def test_login_matches_unicode_emails_in_any_normalization_form(client: ApiClient, make_client) -> None:
    """Registration stores NFC; a login typed in decomposed form (e + combining diaeresis) used to never match."""
    composed, decomposed = "zoë@example.com", "Zoë@EXAMPLE.com"
    assert client.register(decomposed)["email"] == composed
    response = make_client().post(LOGIN, json={"email": decomposed, "password": PASSWORD})
    assert response.status_code == 200 and response.json()["user"]["email"] == composed
    assert make_client().post(LOGIN, json={"email": composed, "password": PASSWORD}).status_code == 200
    duplicate = make_client().post("/api/v1/auth/register", json={"email": composed, "password": PASSWORD, "display_name": "Z"})
    assert duplicate.status_code == 409
    # Not an address at all: still the uniform 401, not a validation error.
    assert make_client().post(LOGIN, json={"email": "not an email", "password": PASSWORD}).status_code == 401


def test_login_failures_are_indistinguishable(client: ApiClient, make_client) -> None:
    client.register("ada@example.com")
    wrong_password = make_client().post(LOGIN, json={"email": "ada@example.com", "password": "wrong password!!"})
    unknown_user = make_client().post(LOGIN, json={"email": "nobody@example.com", "password": "wrong password!!"})
    for response in (wrong_password, unknown_user):
        assert response.status_code == 401
        assert "tf_session" not in response.cookies
    assert wrong_password.json()["error"]["code"] == unknown_user.json()["error"]["code"] == "invalid_credentials"
    assert wrong_password.json()["error"]["message"] == unknown_user.json()["error"]["message"]
    assert "wrong password" not in wrong_password.text


def test_inactive_user_cannot_login_and_existing_sessions_stop_working(client: ApiClient, make_client, db: Session) -> None:
    client.register("ada@example.com")
    db.execute(update(User).values(is_active=False))
    db.commit()
    assert client.get(ME).status_code == 401
    response = make_client().post(LOGIN, json={"email": "ada@example.com", "password": PASSWORD})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_me_requires_session(client: ApiClient) -> None:
    response = client.get(ME)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_me_with_unknown_token(client: ApiClient) -> None:
    client.cookies.set("tf_session", "forged-token")
    assert client.get(ME).status_code == 401


def test_me_returns_current_user(alice: ApiClient) -> None:
    response = alice.get(ME)
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "alice@example.com"
    assert response.json()["csrf_token"] == alice.headers["X-CSRF-Token"]


def test_expired_session_is_rejected(alice: ApiClient, db: Session) -> None:
    db.execute(update(UserSession).values(expires_at=utcnow() - timedelta(seconds=1)))
    db.commit()
    assert alice.get(ME).status_code == 401


def test_session_expiry_matches_configured_ttl(alice: ApiClient, db: Session, settings) -> None:
    session = db.scalar(select(UserSession))
    assert session is not None
    expected = session.created_at + timedelta(hours=settings.session_ttl_hours)
    assert abs((session.expires_at - expected).total_seconds()) < 5


def test_revoked_session_is_rejected(alice: ApiClient, db: Session) -> None:
    db.execute(update(UserSession).values(revoked_at=utcnow()))
    db.commit()
    assert alice.get(ME).status_code == 401


def test_logout_requires_csrf_header(alice: ApiClient) -> None:
    csrf = alice.headers.pop("X-CSRF-Token")
    missing = alice.post(LOGOUT)
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "csrf_failed"

    alice.headers["X-CSRF-Token"] = "0" * 64
    assert alice.post(LOGOUT).status_code == 403

    alice.headers["X-CSRF-Token"] = csrf
    assert alice.get(ME).status_code == 200  # session survived the rejected attempts


def test_csrf_token_from_another_session_is_rejected(alice: ApiClient, bob: ApiClient) -> None:
    alice.headers["X-CSRF-Token"] = bob.headers["X-CSRF-Token"]
    assert alice.post(LOGOUT).status_code == 403


def test_logout_revokes_session_server_side(alice: ApiClient, db: Session) -> None:
    token = alice.cookies["tf_session"]
    response = alice.post(LOGOUT)
    assert response.status_code == 204
    assert "tf_session" not in alice.cookies

    session = db.scalar(select(UserSession).where(UserSession.token_hash == sha256_hex(token)))
    assert session is not None and session.revoked_at is not None

    # Replaying the old cookie must fail even though the client still holds it.
    alice.cookies.set("tf_session", token)
    assert alice.get(ME).status_code == 401


def test_logout_only_revokes_the_current_session(alice: ApiClient, make_client) -> None:
    second = make_client()
    second.remember_auth(second.post(LOGIN, json={"email": "alice@example.com", "password": PASSWORD}).json())
    assert alice.post(LOGOUT).status_code == 204
    assert second.get(ME).status_code == 200


def test_database_enforces_lowercase_email(db: Session) -> None:
    db.add(User(email="Mixed@Example.com", password_hash="x", display_name="X"))
    with pytest.raises(IntegrityError, match="ck_users_email_lowercase"):
        db.flush()


def test_database_enforces_unique_email(db: Session) -> None:
    db.add_all([User(email="dup@example.com", password_hash="x", display_name="X") for _ in range(2)])
    with pytest.raises(IntegrityError, match="uq_users_email"):
        db.flush()
