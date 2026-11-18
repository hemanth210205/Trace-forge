"""Server-side account administration: ``python -m app.accounts`` (from ``backend/``).

    python -m app.accounts [--database dev|test|main] invite create EMAIL [--days N] [--note TEXT]
    python -m app.accounts [--database ...] invite list [--all]
    python -m app.accounts [--database ...] invite revoke ID_OR_EMAIL
    python -m app.accounts [--database ...] invite prune [--older-than-days N]
    python -m app.accounts [--database ...] user create EMAIL --name NAME [--password-stdin]

Like ``app.serve``, the database is ``dev`` unless ``--database`` says otherwise; ``main`` is never a default and
must already be at the code's schema revision (this tool never migrates). Running it requires shell access to the
server, which is what makes it the place to create the first account when registration is ``disabled`` or
``invite``. Invitation codes are printed once to standard output and never logged; only their hashes are stored.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable
from typing import TextIO

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import UnsafeDatabase, database_name, parse_choice, resolve_database_url
from app.core.security import hash_password
from app.db.schema import SchemaOutOfDate, ensure_current
from app.db.session import CONNECT_ARGS
from app.models import User
from app.schemas.auth import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, canonical_email
from app.services import invitations


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.accounts", description="Manage accounts and invitations.")
    parser.add_argument("--database", choices=("dev", "test", "main"), default="dev",
                        help="dev (default), test, or main (explicit only; must be migrated)")
    groups = parser.add_subparsers(dest="group", required=True)

    invite = groups.add_parser("invite", help="Invitations for TRACEFORGE_REGISTRATION=invite").add_subparsers(dest="action", required=True)
    create = invite.add_parser("create", help="Invite one email address; prints the code once")
    create.add_argument("email")
    create.add_argument("--days", type=int, default=None, help="Expiry in days (default: TRACEFORGE_INVITATION_TTL_DAYS)")
    create.add_argument("--note", default="", help="Free text shown by 'invite list', e.g. who asked for it")
    listing = invite.add_parser("list", help="Pending invitations (with --all: used, revoked and expired too)")
    listing.add_argument("--all", action="store_true")
    revoke = invite.add_parser("revoke", help="Revoke pending invitations by id or by email")
    revoke.add_argument("target")
    prune = invite.add_parser("prune", help="Delete used, revoked and expired invitations")
    prune.add_argument("--older-than-days", type=int, default=30)

    user = groups.add_parser("user", help="Accounts").add_subparsers(dest="action", required=True)
    user_create = user.add_parser("create", help="Create an account directly (works in every registration mode)")
    user_create.add_argument("email")
    user_create.add_argument("--name", required=True)
    user_create.add_argument("--password-stdin", action="store_true", help="Read the password from standard input")
    return parser


def _engine_for(settings: Settings, choice: str) -> Engine:
    url = resolve_database_url(settings, parse_choice(choice))
    engine = create_engine(url, connect_args=CONNECT_ARGS)
    try:
        ensure_current(engine, database_name(url))
    except SchemaOutOfDate as exc:
        engine.dispose()
        raise SystemExit(f"Refusing to run: {exc}") from None
    return engine


def main(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    engine: Engine | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    read_password: Callable[[str], str] = getpass.getpass,
) -> int:
    args = _parser().parse_args(argv)
    settings = settings or get_settings()
    out = stdout or sys.stdout
    try:
        engine = engine or _engine_for(settings, args.database)
    except UnsafeDatabase as exc:
        print(exc, file=sys.stderr)
        return 2
    with Session(engine) as db:
        try:
            code = _run(args, settings, db, out, stdin or sys.stdin, read_password)
        except invitations.InvitationError as exc:
            db.rollback()
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        db.commit()
        return code


def _run(args: argparse.Namespace, settings: Settings, db: Session, out: TextIO, stdin: TextIO,
         read_password: Callable[[str], str]) -> int:
    if args.group == "invite" and args.action == "create":
        created = invitations.create(db, args.email, ttl_days=args.days or settings.invitation_ttl_days, note=args.note)
        inv = created.invitation
        print(f"Invitation for {inv.email}, valid until {inv.expires_at:%Y-%m-%d %H:%M} UTC (id {inv.id}).", file=out)
        print(f"Code (shown once): {created.code}", file=out)
        # The code is in the fragment (#...): browsers never send it to the server, so it stays out of access logs.
        print(f"Link: {settings.public_origin}/register#invite={created.code}", file=out)
        if settings.registration != "invite":
            print(f"Note: TRACEFORGE_REGISTRATION is '{settings.registration}', so invitations are not required yet.", file=out)
        return 0
    if args.group == "invite" and args.action == "list":
        rows = invitations.listing(db, include_inactive=args.all)
        if not rows:
            print("No invitations." if args.all else "No pending invitations.", file=out)
        for inv in rows:
            note = f"  ({inv.note})" if inv.note else ""
            print(f"{inv.id}  {invitations.status(inv):8}  {inv.email}  expires {inv.expires_at:%Y-%m-%d}{note}", file=out)
        return 0
    if args.group == "invite" and args.action == "revoke":
        count = invitations.revoke(db, args.target)
        print(f"Revoked {count} pending invitation(s).", file=out)
        return 0 if count else 1
    if args.group == "invite" and args.action == "prune":
        print(f"Deleted {invitations.prune(db, older_than_days=args.older_than_days)} invitation(s).", file=out)
        return 0
    if args.group == "user" and args.action == "create":
        return _create_user(args, db, out, stdin, read_password)
    raise AssertionError(args)


def _create_user(args: argparse.Namespace, db: Session, out: TextIO, stdin: TextIO, read_password: Callable[[str], str]) -> int:
    from email_validator import EmailNotValidError, validate_email

    try:
        validate_email(args.email.strip(), check_deliverability=False)
    except EmailNotValidError as exc:
        raise invitations.InvitationError(f"Invalid email address: {exc}") from None
    email = canonical_email(args.email)
    name = args.name.strip()[:100]
    if not name:
        raise invitations.InvitationError("--name must not be blank.")
    if args.password_stdin:
        password = stdin.readline().rstrip("\r\n")
    else:
        password = read_password("Password: ")
        if read_password("Repeat password: ") != password:
            raise invitations.InvitationError("The passwords do not match.")
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise invitations.InvitationError(f"The password needs {PASSWORD_MIN_LENGTH} to {PASSWORD_MAX_LENGTH} characters.")
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise invitations.InvitationError("An account with this email already exists.")
    db.add(User(email=email, password_hash=hash_password(password), display_name=name))
    try:
        db.flush()
    except IntegrityError:
        raise invitations.InvitationError("An account with this email already exists.") from None
    print(f"Created account {email}.", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
