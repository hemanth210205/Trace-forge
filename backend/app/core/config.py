from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime configuration, read from the environment and the repository-root .env file."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # The main database. Also the reference for the dev/test names; only connected to when explicitly selected
    # (`python -m app.serve --database main`). See app/core/database.py.
    database_url: SecretStr = Field(validation_alias="DATABASE_URL")
    test_database_url: SecretStr | None = Field(default=None, validation_alias="TEST_DATABASE_URL")
    # The default database for every server start (`python -m app.serve`).
    dev_database_url: SecretStr | None = Field(default=None, validation_alias="DEV_DATABASE_URL")

    secret_key: SecretStr = Field(validation_alias="TRACEFORGE_SECRET_KEY")
    api_key_pepper: SecretStr = Field(validation_alias="TRACEFORGE_API_KEY_PEPPER")

    session_ttl_hours: int = Field(default=168, ge=1, le=24 * 90, validation_alias="TRACEFORGE_SESSION_TTL_HOURS")
    cookie_secure: bool = Field(default=False, validation_alias="TRACEFORGE_COOKIE_SECURE")
    max_request_bytes: int = Field(
        default=5 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024, validation_alias="TRACEFORGE_MAX_REQUEST_BYTES"
    )
    # Also the trusted origins for the Origin check on login and registration.
    cors_origins: str = Field(default="http://localhost:5173", validation_alias="TRACEFORGE_CORS_ORIGINS")
    log_level: str = Field(default="INFO", validation_alias="TRACEFORGE_LOG_LEVEL")
    # Host names the server answers to (Host header, port ignored). Localhost only: this also blocks DNS-rebinding
    # pages, which is what makes trusting same-origin console requests safe.
    allowed_hosts: str = Field(default="127.0.0.1,localhost", validation_alias="TRACEFORGE_ALLOWED_HOSTS")
    # The built console (npm run build). Served at / when its index.html exists; empty disables serving.
    frontend_dist: str = Field(default=str(REPO_ROOT / "frontend" / "dist"), validation_alias="TRACEFORGE_FRONTEND_DIST")

    # Failed-login rate limiting (in-process; see app/core/rate_limit.py for deployment limits).
    login_max_failures: int = Field(default=5, ge=1, le=1000, validation_alias="TRACEFORGE_LOGIN_MAX_FAILURES")
    login_max_failures_per_ip: int = Field(
        default=50, ge=1, le=100_000, validation_alias="TRACEFORGE_LOGIN_MAX_FAILURES_PER_IP"
    )
    login_failure_window_seconds: int = Field(
        default=900, ge=1, le=24 * 3600, validation_alias="TRACEFORGE_LOGIN_FAILURE_WINDOW_SECONDS"
    )
    # Failure diagnosis (M6). "mock" is the deterministic offline provider and needs no key.
    diagnosis_provider: Literal["mock", "anthropic"] = Field(default="mock", validation_alias="TRACEFORGE_DIAGNOSIS_PROVIDER")
    diagnosis_model: str = Field(default="claude-opus-5-5", max_length=200, validation_alias="TRACEFORGE_DIAGNOSIS_MODEL")
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    diagnosis_provider_timeout_seconds: float = Field(
        default=60.0, ge=1, le=600, validation_alias="TRACEFORGE_DIAGNOSIS_PROVIDER_TIMEOUT_SECONDS"
    )
    diagnosis_provider_max_retries: int = Field(default=2, ge=0, le=5, validation_alias="TRACEFORGE_DIAGNOSIS_PROVIDER_MAX_RETRIES")
    diagnosis_max_cases: int = Field(default=10, ge=1, le=100, validation_alias="TRACEFORGE_DIAGNOSIS_MAX_CASES")
    # Controlled experiments (M7): bounds on what one experiment may replay and for how long.
    experiment_max_cases: int = Field(default=500, ge=1, le=5000, validation_alias="TRACEFORGE_EXPERIMENT_MAX_CASES")
    experiment_time_limit_seconds: float = Field(
        default=3600.0, ge=10, le=86_400, validation_alias="TRACEFORGE_EXPERIMENT_TIME_LIMIT_SECONDS"
    )
    diagnosis_time_limit_seconds: float = Field(default=300.0, ge=5, le=3600, validation_alias="TRACEFORGE_DIAGNOSIS_TIME_LIMIT_SECONDS")
    # Recovery of interrupted work (app/services/recovery.py): a sweep at startup, then every interval (0 = startup only).
    recovery_interval_seconds: float = Field(default=60.0, ge=0, le=3600, validation_alias="TRACEFORGE_RECOVERY_INTERVAL_SECONDS")
    stale_job_grace_seconds: float = Field(default=120.0, ge=10, le=3600, validation_alias="TRACEFORGE_STALE_JOB_GRACE_SECONDS")

    # Sign in with Google / GitHub (OAuth 2.0). A provider is offered only when both its id and secret are set.
    # public_url is the origin the browser uses for the console; redirect URIs are <public_url>/api/v1/auth/oauth/
    # <provider>/callback and must be registered exactly like that with the provider.
    public_url: str = Field(default="http://127.0.0.1:8000", validation_alias="TRACEFORGE_PUBLIC_URL")
    google_client_id: str | None = Field(default=None, validation_alias="TRACEFORGE_GOOGLE_CLIENT_ID")
    google_client_secret: SecretStr | None = Field(default=None, validation_alias="TRACEFORGE_GOOGLE_CLIENT_SECRET")
    github_client_id: str | None = Field(default=None, validation_alias="TRACEFORGE_GITHUB_CLIENT_ID")
    github_client_secret: SecretStr | None = Field(default=None, validation_alias="TRACEFORGE_GITHUB_CLIENT_SECRET")

    login_limiter_max_keys: int = Field(
        default=10_000, ge=100, le=1_000_000, validation_alias="TRACEFORGE_LOGIN_LIMITER_MAX_KEYS"
    )

    # Reverse proxies whose X-Forwarded-For / X-Forwarded-Proto are believed (IPs or CIDRs, comma-separated).
    # Empty (the default): forwarded headers are ignored and the direct peer is the client. See app/core/proxy.py.
    trusted_proxies: str = Field(default="", validation_alias="TRACEFORGE_TRUSTED_PROXIES")

    # Who may create an account (by password or through Google/GitHub). Existing accounts can always sign in.
    #   open: anyone; disabled: nobody (create accounts with `python -m app.accounts`);
    #   domain: emails in TRACEFORGE_REGISTRATION_DOMAINS; invite: holders of an invitation (app/services/invitations.py).
    registration: Literal["open", "disabled", "domain", "invite"] = Field(default="open", validation_alias="TRACEFORGE_REGISTRATION")
    registration_domains: str = Field(default="", validation_alias="TRACEFORGE_REGISTRATION_DOMAINS")
    invitation_ttl_days: int = Field(default=7, ge=1, le=90, validation_alias="TRACEFORGE_INVITATION_TTL_DAYS")

    # Hosting platforms without a shell or pre-deploy step (Render's free plan). Both are off by default.
    # public_bind: allow `app.serve --host 0.0.0.0` behind the platform's TLS proxy (requires trusted proxies and
    # secure cookies). migrate_main_on_start: the main database's exact name, as confirmation, to upgrade it to head
    # when the server starts (upgrades only; never downgrades). Anything else, including "true", is refused.
    public_bind: bool = Field(default=False, validation_alias="TRACEFORGE_PUBLIC_BIND")
    migrate_main_on_start: str = Field(default="", validation_alias="TRACEFORGE_MIGRATE_MAIN_ON_START")

    @field_validator("secret_key", "api_key_pepper")
    @classmethod
    def _long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("must be at least 32 characters; see .env.example")
        return value

    @field_validator("trusted_proxies")
    @classmethod
    def _valid_proxies(cls, value: str) -> str:
        from app.core.proxy import parse_networks

        try:
            parse_networks(value)
        except ValueError as exc:
            raise ValueError(f"must be comma-separated IP addresses or CIDR ranges ({exc})") from None
        return value

    @field_validator("database_url", "test_database_url", "dev_database_url", mode="before")
    @classmethod
    def _psycopg_driver(cls, value: object) -> object:
        """Hosting platforms (Render, Heroku and others) hand out ``postgres://`` or ``postgresql://`` URLs; SQLAlchemy
        needs the driver named. Only the scheme is rewritten; host, credentials and parameters are untouched."""
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(raw, str):
            for prefix in ("postgres://", "postgresql://"):
                if raw.startswith(prefix):
                    return "postgresql+psycopg://" + raw[len(prefix):]
        return value

    @model_validator(mode="after")
    def _domains_for_domain_mode(self) -> "Settings":
        if self.registration == "domain" and not self.registration_domain_list:
            raise ValueError("TRACEFORGE_REGISTRATION=domain needs TRACEFORGE_REGISTRATION_DOMAINS (e.g. example.com)")
        return self

    @model_validator(mode="after")
    def _public_bind_needs_a_proxy(self) -> "Settings":
        # Listening on a public interface is only safe when a TLS-terminating platform proxy is the only way in:
        # without trusted proxies the rate limiter sees one address for everyone, and without secure cookies sessions
        # would travel in clear text.
        if self.public_bind and (not self.trusted_proxies.strip() or not self.cookie_secure):
            raise ValueError(
                "TRACEFORGE_PUBLIC_BIND=true requires TRACEFORGE_TRUSTED_PROXIES and TRACEFORGE_COOKIE_SECURE=true"
            )
        return self

    @property
    def trusted_proxy_networks(self) -> list:
        from app.core.proxy import parse_networks

        return parse_networks(self.trusted_proxies)

    @property
    def registration_domain_list(self) -> list[str]:
        return [d.strip().lower().lstrip("@") for d in self.registration_domains.split(",") if d.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip().lower() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def public_origin(self) -> str:
        return self.public_url.strip().rstrip("/")

    @property
    def oauth_providers(self) -> list[str]:
        """Providers with both a client id and a secret configured, in display order."""
        configured = {
            "google": bool(self.google_client_id and self.google_client_secret and self.google_client_secret.get_secret_value()),
            "github": bool(self.github_client_id and self.github_client_secret and self.github_client_secret.get_secret_value()),
        }
        return [name for name in ("google", "github") if configured[name]]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
