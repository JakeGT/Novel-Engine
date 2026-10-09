from typing import Literal
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_NEO4J_PASSWORD = "password"
DEFAULT_POSTGRES_PASSWORD = "postgres"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    PROJECT_NAME: str = "Novel Engine"
    PROJECT_VERSION: str = "0.1.0"
    APP_ENV: Literal["development", "test", "production"] = "development"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Error monitoring (optional; an empty backend DSN disables it entirely).
    # See backend/core/observability.py for the privacy scrubbing applied.
    SENTRY_DSN_BACKEND: str = ""
    SENTRY_DSN_WEB: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0
    # Operational metrics (Prometheus text format at /health/metrics). Off by
    # default. A token is required outside local development; without one the
    # endpoint refuses to serve in production.
    METRICS_ENABLED: bool = False
    METRICS_TOKEN: str = ""

    OPENAI_API_KEY: str = ""
    AI_MODEL: str = "gpt-4o"
    # OpenAI-compatible provider label and optional base URL (OpenRouter,
    # Ollama, llama.cpp, etc.). Empty base URL means the OpenAI default endpoint.
    AI_PROVIDER: str = "openai"
    AI_BASE_URL: str = ""

    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = DEFAULT_NEO4J_PASSWORD
    NEO4J_DATABASE: str = "neo4j"

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "novel"
    POSTGRES_PASSWORD: str = DEFAULT_POSTGRES_PASSWORD
    POSTGRES_DB: str = "novel_engine"
    POSTGRES_POOL_SIZE: int = 5
    POSTGRES_MAX_OVERFLOW: int = 10
    POSTGRES_POOL_TIMEOUT: float = 30.0

    EMBEDDING_MODEL_NAME: str = "all-MiniLM-L6-v2"
    EMBEDDING_DIMENSION: int = 384
    CUSTOM_SCHEMA_PATH: str = "backend/config/custom_schema.json"
    ENABLE_DEBUG_ARTIFACTS: bool = False
    ARTIFACT_STORE_DIR: str = "backend/debug"
    OBJECT_STORE_DIR: str = "data/objects"

    # Authentication. "local" is the trusted single-user self-hosted mode;
    # "hosted" delegates to a shared OIDC provider (Authentik).
    AUTH_MODE: Literal["local", "hosted"] = "local"
    AUTH_LOCAL_USER_ID: str = "local-user"
    AUTH_LOCAL_WORKSPACE_ID: str = "local-workspace"
    AUTH_LOCAL_PROJECT_ID: str = "local-project"

    OIDC_ISSUER: str = ""
    OIDC_CLIENT_ID: str = ""
    OIDC_CLIENT_SECRET: str = ""
    OIDC_REDIRECT_URI: str = ""
    OIDC_POST_LOGOUT_REDIRECT_URI: str = ""
    OIDC_SCOPES: str = "openid profile email"

    SESSION_COOKIE_NAME: str = "novel_session"
    SESSION_TTL_SECONDS: int = 60 * 60 * 24 * 14
    SESSION_ROTATION_SECONDS: int = 60 * 60
    CSRF_COOKIE_NAME: str = "novel_csrf"

    # Comma-separated local user ids granted platform-wide authority (global schema,
    # workspace provisioning). Local mode always counts as platform administrator.
    PLATFORM_ADMIN_USER_IDS: str = ""
    # Hosted workspaces are invitation-gated: only platform admins may create one.
    ALLOW_WORKSPACE_SELF_CREATION: bool = False

    # Provider credentials and usage limits. An empty KEK disables credential storage.
    CREDENTIAL_KEK: str = ""
    CREDENTIAL_KEK_VERSION: int = 1
    # Retired KEKs kept only for decrypting existing credentials, as "version:kek"
    # pairs (comma-separated), so rotating CREDENTIAL_KEK does not strand them.
    CREDENTIAL_PREVIOUS_KEKS: str = ""
    PROVIDER_PER_JOB_TOKEN_LIMIT: int = 200_000
    PROVIDER_PER_JOB_COST_LIMIT: float = 0.0
    PROVIDER_PERIOD_TOKEN_LIMIT: int = 2_000_000
    PROVIDER_PERIOD_COST_LIMIT: float = 0.0
    PROVIDER_PERIOD_SECONDS: int = 60 * 60 * 24

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def is_hosted(self) -> bool:
        return self.AUTH_MODE == "hosted"

    @property
    def oidc_scopes(self) -> list[str]:
        return [scope for scope in self.OIDC_SCOPES.split() if scope]

    @property
    def session_cookie_secure(self) -> bool:
        """Cookies are always Secure under hosted/production; plain only in local dev/test."""
        return self.AUTH_MODE == "hosted" or self.APP_ENV == "production"

    @property
    def platform_admin_user_ids(self) -> set[str]:
        return {value.strip() for value in self.PLATFORM_ADMIN_USER_IDS.split(",") if value.strip()}

    @property
    def previous_credential_keks(self) -> dict[int, str]:
        keys: dict[int, str] = {}
        for pair in self.CREDENTIAL_PREVIOUS_KEKS.split(","):
            entry = pair.strip()
            if not entry or ":" not in entry:
                continue
            version, _, kek = entry.partition(":")
            try:
                keys[int(version.strip())] = kek.strip()
            except ValueError:
                continue
        return keys

    def _postgres_dsn(self, scheme: str) -> str:
        user = quote_plus(self.POSTGRES_USER)
        password = quote_plus(self.POSTGRES_PASSWORD)
        return (
            f"{scheme}://{user}:{password}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def postgres_dsn(self) -> str:
        """SQLAlchemy async DSN used by the operational database adapter."""
        return self._postgres_dsn("postgresql+psycopg")

    @property
    def postgres_dsn_psycopg(self) -> str:
        """Plain psycopg DSN for synchronous infrastructure such as the pgvector adapter."""
        return self._postgres_dsn("postgresql")

    @model_validator(mode="after")
    def _reject_unsafe_production_defaults(self) -> "Settings":
        if self.APP_ENV == "production":
            if self.NEO4J_PASSWORD == DEFAULT_NEO4J_PASSWORD:
                raise ValueError(
                    "NEO4J_PASSWORD must not use the development default in production"
                )
            if self.POSTGRES_PASSWORD == DEFAULT_POSTGRES_PASSWORD:
                raise ValueError(
                    "POSTGRES_PASSWORD must not use the development default in production"
                )
        return self

    @model_validator(mode="after")
    def _validate_auth_configuration(self) -> "Settings":
        if self.AUTH_MODE == "hosted":
            missing = [
                name
                for name, value in (
                    ("OIDC_ISSUER", self.OIDC_ISSUER),
                    ("OIDC_CLIENT_ID", self.OIDC_CLIENT_ID),
                    ("OIDC_CLIENT_SECRET", self.OIDC_CLIENT_SECRET),
                    ("OIDC_REDIRECT_URI", self.OIDC_REDIRECT_URI),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "AUTH_MODE=hosted requires OIDC configuration: " + ", ".join(missing)
                )
            if not self.OIDC_ISSUER.startswith("https://") or "*" in self.OIDC_ISSUER:
                raise ValueError("OIDC_ISSUER must be an exact https:// issuer URL")
            if not self.OIDC_REDIRECT_URI.startswith("https://") or "*" in self.OIDC_REDIRECT_URI:
                raise ValueError("OIDC_REDIRECT_URI must be an exact https:// URL")
            if "openid" not in self.oidc_scopes:
                raise ValueError("OIDC_SCOPES must include 'openid'")
        return self


settings = Settings()
