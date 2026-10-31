"""Password hashing, session tokens, CSRF tokens and API key primitives."""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # argon2id with library defaults
# Verified against when the user does not exist, so login timing does not reveal whether an email is registered.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

API_KEY_PATTERN = re.compile(r"^tf_([0-9a-f]{8})_([A-Za-z0-9_\-]{32,64})$")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_token_for(session_token: str, secret_key: str) -> str:
    """CSRF token bound to the session; recomputable server-side, so nothing extra is stored."""
    return hmac.new(secret_key.encode(), f"csrf:{session_token}".encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class GeneratedApiKey:
    prefix: str
    secret: str  # full key, shown to the user once


def generate_api_key() -> GeneratedApiKey:
    prefix = secrets.token_hex(4)
    return GeneratedApiKey(prefix=prefix, secret=f"tf_{prefix}_{secrets.token_urlsafe(32)}")


def hash_api_key(full_key: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), full_key.encode(), hashlib.sha256).hexdigest()


def parse_api_key(full_key: str) -> str | None:
    """Returns the key prefix if the key is well-formed."""
    match = API_KEY_PATTERN.match(full_key)
    return match.group(1) if match else None


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
