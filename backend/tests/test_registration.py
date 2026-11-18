"""Registration modes (TRACEFORGE_REGISTRATION), invitations, and first-account bootstrap with ``app.accounts``.

The policy must hold for both ways an account is created: the password form and Google/GitHub sign-in.
"""

import io
import logging
import threading
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from app import accounts
from app.core.config import Settings
from app.db.base import utcnow
from app.models import Invitation, OAuthIdentity, User
from app.services import invitations
from tests.conftest import PASSWORD, TEST_API_KEY_PEPPER, TEST_SECRET_KEY, ApiClient
from tests.test_oauth import PUBLIC, FakeProviders, providers, sign_in  # noqa: F401 (fixture)

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"


@pytest.fixture
def policy(app: FastAPI) -> Iterator[Any]:
    """policy("invite") switches the running app's registration mode for one test (layered on any OAuth config)."""
    original = app.state.settings

    def apply(mode: str, domains: str = "") -> Settings:
        app.state.settings = app.state.settings.model_copy(update={"registration": mode, "registration_domains": domains})
        return app.state.settings

    yield apply
    app.state.settings = original


def register(client: ApiClient, email: str, invite_code: str | None = None) -> Any:
    body: dict[str, Any] = {"email": email, "password": PASSWORD, "display_name": "New User"}
    if invite_code is not None:
        body["invite_code"] = invite_code
    return client.post(REGISTER, json=body)


def invite(engine: Engine, email: str, *, days: int = 7) -> invitations.CreatedInvitation:
    with Session(engine) as db:
        created = invitations.create(db, email, ttl_days=days)
        db.commit()
        db.refresh(created.invitation)
        db.expunge(created.invitation)
        return created


def users(db: Session) -> list[str]:
    db.expire_all()
    return sorted(db.scalars(select(User.email)))


# --- configuration ---------------------------------------------------------------------------------------------


def test_registration_mode_is_published(client: ApiClient, policy: Any) -> None:
    assert client.get("/api/v1/auth/registration").json() == {"mode": "open"}
    policy("invite")
    assert client.get("/api/v1/auth/registration").json() == {"mode": "invite"}


def test_domain_mode_requires_domains() -> None:
    with pytest.raises(ValidationError, match="TRACEFORGE_REGISTRATION_DOMAINS"):
        Settings(TRACEFORGE_SECRET_KEY=TEST_SECRET_KEY, TRACEFORGE_API_KEY_PEPPER=TEST_API_KEY_PEPPER,  # type: ignore[call-arg]
                 TRACEFORGE_REGISTRATION="domain")


# --- open (default) ---------------------------------------------------------------------------------------------


def test_open_registration_is_unchanged(client: ApiClient, db: Session) -> None:
    assert register(client, "ada@example.com").status_code == 201
    assert users(db) == ["ada@example.com"]


# --- disabled ---------------------------------------------------------------------------------------------------


def test_disabled_refuses_password_registration_without_revealing_existing_emails(
    make_client: Any, policy: Any, db: Session
) -> None:
    make_client().register("taken@example.com")
    policy("disabled")
    for email in ("new@example.com", "taken@example.com"):  # same answer whether or not the email exists
        response = register(make_client(), email)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "registration_closed"
    assert users(db) == ["taken@example.com"]


def test_disabled_still_lets_existing_accounts_sign_in(make_client: Any, policy: Any) -> None:
    make_client().register("ada@example.com")
    policy("disabled")
    assert make_client().post(LOGIN, json={"email": "ada@example.com", "password": PASSWORD}).status_code == 200


def test_disabled_refuses_new_oauth_accounts_but_not_existing_ones(
    make_client: Any, providers: FakeProviders, policy: Any, db: Session  # noqa: F811
) -> None:
    policy("disabled")
    assert sign_in(make_client(), "github") == f"{PUBLIC}/login?oauth_error=registration_closed"
    assert users(db) == []
    # An existing account (created by an administrator) can still sign in with GitHub.
    policy("open")
    make_client().register("ada@example.com")
    policy("disabled")
    client = make_client()
    assert sign_in(client, "github") == f"{PUBLIC}/"
    assert client.get(ME).json()["user"]["email"] == "ada@example.com"


# --- domain -----------------------------------------------------------------------------------------------------


def test_domain_mode_for_passwords(make_client: Any, policy: Any, db: Session) -> None:
    policy("domain", "example.com, @Team.Example.ORG")
    assert register(make_client(), "ada@example.com").status_code == 201
    assert register(make_client(), "bob@team.example.org").status_code == 201
    refused = register(make_client(), "eve@evil.example")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "domain_not_allowed"
    assert register(make_client(), "eve@sub.example.com").status_code == 403  # subdomains are not implied
    assert users(db) == ["ada@example.com", "bob@team.example.org"]


def test_domain_mode_for_oauth(make_client: Any, providers: FakeProviders, policy: Any, db: Session) -> None:  # noqa: F811
    policy("domain", "company.example")
    providers.github_emails = [{"email": "ada@example.com", "verified": True, "primary": True}]
    assert sign_in(make_client(), "github").endswith("oauth_error=domain_not_allowed")
    providers.github_emails = [{"email": "ada@company.example", "verified": True, "primary": True}]
    assert sign_in(make_client(), "github") == f"{PUBLIC}/"
    assert users(db) == ["ada@company.example"]


# --- invite: password registration -----------------------------------------------------------------------------


def test_invite_mode_requires_a_code(client: ApiClient, policy: Any, db: Session) -> None:
    policy("invite")
    response = register(client, "ada@example.com")
    assert response.status_code == 403 and response.json()["error"]["code"] == "invitation_required"
    assert users(db) == []


def test_valid_invitation_creates_the_account_once(make_client: Any, policy: Any, engine: Engine, db: Session) -> None:
    policy("invite")
    created = invite(engine, "Ada@Example.com")
    assert register(make_client(), "ada@example.com", created.code).status_code == 201
    row = db.get(Invitation, created.invitation.id)
    assert row is not None and row.used_at is not None
    assert row.used_by_user_id == db.scalar(select(User.id).where(User.email == "ada@example.com"))
    # The same code cannot be used again, for any email.
    for email in ("ada@example.com", "other@example.com"):
        again = register(make_client(), email, created.code)
        assert again.status_code in (403, 409)
    assert register(make_client(), "other@example.com", created.code).json()["error"]["code"] == "invitation_invalid"


def test_code_is_tied_to_its_email(client: ApiClient, policy: Any, engine: Engine, db: Session) -> None:
    policy("invite")
    created = invite(engine, "ada@example.com")
    response = register(client, "mallory@example.com", created.code)
    assert response.status_code == 403 and response.json()["error"]["code"] == "invitation_invalid"
    assert users(db) == []  # the account row was rolled back with the refusal
    assert register(client, "ada@example.com", created.code).status_code == 201  # still pending


@pytest.mark.parametrize("state", ["expired", "revoked"])
def test_expired_or_revoked_invitations_are_refused(client: ApiClient, policy: Any, engine: Engine, db: Session, state: str) -> None:
    policy("invite")
    created = invite(engine, "ada@example.com")
    with Session(engine) as s:
        if state == "expired":
            s.execute(update(Invitation).values(created_at=utcnow() - timedelta(days=9), expires_at=utcnow() - timedelta(days=2)))
        else:
            assert invitations.revoke(s, "ada@example.com") == 1
        s.commit()
    response = register(client, "ada@example.com", created.code)
    assert response.status_code == 403 and response.json()["error"]["code"] == "invitation_invalid"
    assert users(db) == []


def test_wrong_code_is_refused(client: ApiClient, policy: Any, engine: Engine) -> None:
    policy("invite")
    invite(engine, "ada@example.com")
    assert register(client, "ada@example.com", "not-the-code").json()["error"]["code"] == "invitation_invalid"


def test_failed_account_creation_leaves_the_invitation_pending(make_client: Any, policy: Any, engine: Engine, db: Session) -> None:
    policy("invite")
    created = invite(engine, "ada@example.com")
    # Someone else creates that account first (here: an administrator), so registration fails with 409.
    assert accounts.main(["user", "create", "ada@example.com", "--name", "Ada", "--password-stdin"],
                         engine=engine, stdin=io.StringIO(PASSWORD + "\n"), stdout=io.StringIO()) == 0
    assert register(make_client(), "ada@example.com", created.code).status_code == 409
    db.expire_all()
    assert invitations.status(db.get(Invitation, created.invitation.id)) == "pending"  # type: ignore[arg-type]


def test_invitation_cannot_be_created_for_an_existing_account(make_client: Any, engine: Engine) -> None:
    make_client().register("ada@example.com")
    with Session(engine) as db, pytest.raises(invitations.InvitationError, match="already exists"):
        invitations.create(db, "ADA@example.com", ttl_days=7)


# --- invite: OAuth ----------------------------------------------------------------------------------------------


def test_oauth_uses_a_pending_invitation_for_the_verified_email(
    make_client: Any, providers: FakeProviders, policy: Any, engine: Engine, db: Session  # noqa: F811
) -> None:
    policy("invite")
    assert sign_in(make_client(), "google").endswith("oauth_error=invitation_invalid")
    assert users(db) == []
    created = invite(engine, "ada@example.com")
    assert sign_in(make_client(), "google") == f"{PUBLIC}/"
    assert users(db) == ["ada@example.com"]
    db.expire_all()
    assert invitations.status(db.get(Invitation, created.invitation.id)) == "used"  # type: ignore[arg-type]
    # Linking another provider later is a sign-in to the existing account, not a new registration.
    assert sign_in(make_client(), "github") == f"{PUBLIC}/"
    assert len(db.scalars(select(OAuthIdentity)).all()) == 2


def test_oauth_cannot_use_an_invitation_for_a_different_email(
    make_client: Any, providers: FakeProviders, policy: Any, engine: Engine, db: Session  # noqa: F811
) -> None:
    policy("invite")
    invite(engine, "someone-else@example.com")
    assert sign_in(make_client(), "google").endswith("oauth_error=invitation_invalid")
    assert users(db) == []


# --- concurrency ------------------------------------------------------------------------------------------------


def test_concurrent_use_of_one_invitation_succeeds_exactly_once(engine: Engine, policy: Any) -> None:
    settings = policy("invite")
    created = invite(engine, "ada@example.com")
    with Session(engine) as setup:
        for email in ("first@example.com", "second@example.com"):
            setup.add(User(email=email, password_hash=None, display_name=email))
        setup.commit()
        first_id, second_id = (setup.scalar(select(User.id).where(User.email == e)) for e in ("first@example.com", "second@example.com"))
    # Both transactions present the same code for the invited email (user rows differ only so the FK is valid).
    holder_a = User(id=first_id, email="ada@example.com", display_name="a")
    holder_b = User(id=second_id, email="ada@example.com", display_name="b")
    outcome: dict[str, Any] = {}
    a, b = Session(engine), Session(engine)
    try:
        assert invitations.consume_for_new_account(a, settings, holder_a, code=created.code, verified_email=False)

        def contender() -> None:
            try:
                outcome["b"] = invitations.consume_for_new_account(b, settings, holder_b, code=created.code, verified_email=False)
            except invitations.RegistrationRefused as exc:
                outcome["b"] = exc.code

        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(timeout=1.0)
        assert thread.is_alive(), "the second transaction must wait for the first one's row lock"
        a.commit()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert outcome["b"] == "invitation_invalid"
    finally:
        b.rollback()
        a.close()
        b.close()
    with Session(engine) as check:
        row = check.get(Invitation, created.invitation.id)
        assert row is not None and row.used_by_user_id == first_id


# --- bootstrap and CLI ------------------------------------------------------------------------------------------


def run_cli(engine: Engine, settings: Settings, *argv: str, stdin: str = "") -> tuple[int, str]:
    out = io.StringIO()
    code = accounts.main(list(argv), settings=settings, engine=engine, stdin=io.StringIO(stdin), stdout=out)
    return code, out.getvalue()


def test_bootstrap_first_account_when_registration_is_disabled(make_client: Any, policy: Any, engine: Engine) -> None:
    settings = policy("disabled")
    assert run_cli(engine, settings, "user", "create", "Admin@Example.com", "--name", "Admin", "--password-stdin", stdin=PASSWORD + "\n")[0] == 0
    login = make_client().post(LOGIN, json={"email": "admin@example.com", "password": PASSWORD})
    assert login.status_code == 200
    assert run_cli(engine, settings, "user", "create", "admin@example.com", "--name", "Again", "--password-stdin", stdin=PASSWORD + "\n")[0] == 1
    assert run_cli(engine, settings, "user", "create", "short@example.com", "--name", "S", "--password-stdin", stdin="short\n")[0] == 1


def test_bootstrap_first_account_with_an_invitation(make_client: Any, policy: Any, engine: Engine, caplog: Any) -> None:
    settings = policy("invite")
    caplog.set_level(logging.DEBUG)
    status, output = run_cli(engine, settings, "invite", "create", "ada@example.com", "--days", "3", "--note", "first admin")
    assert status == 0
    code = output.split("Code (shown once): ", 1)[1].split()[0]
    assert f"/register#invite={code}" in output  # in the fragment: never sent to the server
    assert register(make_client(), "ada@example.com", code).status_code == 201
    # The code never reaches the logs, and only its hash is stored.
    assert code not in caplog.text
    with Session(engine) as db:
        stored = db.scalar(select(Invitation.token_hash))
        assert stored != code and stored == invitations.hash_code(code)
    _, listed = run_cli(engine, settings, "invite", "list", "--all")
    assert "used" in listed and "first admin" in listed and code not in listed


def test_cli_list_revoke_and_prune(policy: Any, engine: Engine) -> None:
    settings = policy("invite")
    run_cli(engine, settings, "invite", "create", "a@example.com")
    run_cli(engine, settings, "invite", "create", "b@example.com")
    assert run_cli(engine, settings, "invite", "list")[1].count("pending") == 2
    assert run_cli(engine, settings, "invite", "revoke", "a@example.com") == (0, "Revoked 1 pending invitation(s).\n")
    assert run_cli(engine, settings, "invite", "revoke", "a@example.com")[0] == 1  # nothing left to revoke
    assert run_cli(engine, settings, "invite", "list")[1].count("pending") == 1
    with Session(engine) as db:
        db.execute(update(Invitation).values(created_at=utcnow() - timedelta(days=60), expires_at=utcnow() - timedelta(days=50)))
        db.commit()
    assert run_cli(engine, settings, "invite", "prune", "--older-than-days", "30") == (0, "Deleted 2 invitation(s).\n")
    assert run_cli(engine, settings, "invite", "create", "not-an-email")[0] == 1


def test_cli_defaults_to_the_dev_database_and_never_to_main() -> None:
    # Parsing only: this test deliberately connects to no database.
    parser = accounts._parser()
    assert parser.parse_args(["invite", "list"]).database == "dev"
    assert parser.parse_args(["--database", "main", "invite", "list"]).database == "main"
    with pytest.raises(SystemExit):
        parser.parse_args(["--database", "production", "invite", "list"])


@pytest.mark.parametrize("mode", ["disabled", "invite"])
def test_existing_oauth_only_user_still_signs_in_when_registration_is_restricted(
    make_client: Any, providers: FakeProviders, policy: Any, db: Session, mode: str  # noqa: F811
) -> None:
    # The account was created through GitHub while registration was open; it has no password.
    assert sign_in(make_client(), "github") == f"{PUBLIC}/"
    user = db.scalar(select(User))
    assert user is not None and user.password_hash is None
    policy(mode)
    client = make_client()
    assert sign_in(client, "github") == f"{PUBLIC}/"
    assert client.get(ME).json()["user"]["email"] == "ada@example.com"
    assert users(db) == ["ada@example.com"]  # signed in, nothing new created
