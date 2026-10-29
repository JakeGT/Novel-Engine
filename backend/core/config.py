import os
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
        # Container deployments mount secrets as files (one per setting, named
        # after it, e.g. /run/secrets/postgres_password). Environment variables
        # still take precedence. Unset means no secrets directory is read.
        secrets_dir=os.environ.get("NOVEL_ENGINE_SECRETS_DIR") or None,
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
    # What is running, so an error can be traced to a deploy: the image tag (a commit)
    # on a hosted site. Empty falls back to PROJECT_VERSION.
    APP_RELEASE: str = ""
    # Operational metrics (Prometheus text format at /health/metrics). Off by
    # default. A token is required outside local development; without one the
    # endpoint refuses to serve in production.
    METRICS_ENABLED: bool = False
    METRICS_TOKEN: str = ""

    # Directory holding the built frontend (index.html + assets/). When set, the
    # API also serves the single-page app, so one container is the whole site.
    FRONTEND_DIST_DIR: str = ""
    # Interactive API docs and the OpenAPI document. Left unset, they are served
    # only by a local, non-production instance; set explicitly to override.
    API_DOCS_ENABLED: bool | None = None

    OPENAI_API_KEY: str = ""
    AI_MODEL: str = "gpt-4o"
    # OpenAI-compatible provider label and optional base URL (OpenRouter,
    # Ollama, llama.cpp, etc.). Empty base URL means the OpenAI default endpoint.
    AI_PROVIDER: str = "openai"
    AI_BASE_URL: str = ""
    # CTX-013: whether the configured model can see pictures, and its limits. Off by
    # default: a route is text only until the operator says otherwise, and a picture is
    # then described in words (recorded as such) or the request is refused before spending.
    AI_IMAGE_INPUT: bool = False
    AI_IMAGE_MAX_COUNT: int = 4
    AI_IMAGE_MAX_BYTES: int = 5 * 1024 * 1024
    AI_IMAGE_TYPES: str = "image/png,image/jpeg,image/webp"
    # The same for a workspace's own provider (hosted): which providers' chosen models are
    # treated as able to see pictures, by provider id ("openrouter,anthropic"); empty is none.
    AI_IMAGE_PROVIDERS: str = ""
    # CTX-013: what each model can take. The two settings above say whether pictures may
    # go to a route at all; within that, a model its provider's list (or this table) says
    # cannot see pictures is given words instead. The table is JSON keyed by
    # "provider/model" (a trailing "*" matches the rest of a name), each with any of
    # "images", "max_images", "context_tokens", "max_output_tokens", "structured":
    # {"openrouter/deepseek/*": {"images": false}}. It wins over a provider's list.
    AI_MODEL_CAPABILITIES: str = ""
    # Read OpenRouter's public list of models (no story words are sent, only the request
    # for the list) to learn each model's limits, at most once a day.
    AI_MODEL_LIST: bool = True
    # SCN-002: ask for drafts as a stream, so the studio shows them as they are written.
    AI_STREAMING: bool = True

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
    # Which model makes search vectors (CTX-011): "minilm" (the model above, text only),
    # "embeddinggemma" (text and pictures in one space; needs the embeddings extra and a
    # pinned revision), "api" (an embedding service over HTTP, below: no model runs on
    # this server, but every passage is sent to that service), "fake" (deterministic
    # stand-in for tests and evaluations) or "none". Changing it needs a rebuild:
    # `make reindex`, then `make reindex-activate`.
    EMBEDDING_PROVIDER: Literal["minilm", "embeddinggemma", "api", "fake", "none"] = "minilm"
    # EMBEDDING_PROVIDER=api: any OpenAI-compatible embeddings endpoint (OpenAI, Google,
    # Mistral, OpenRouter, or Ollama/LM Studio on this machine). https unless local.
    EMBEDDING_API_BASE_URL: str = "https://api.openai.com/v1"
    # A secret: set it in the environment or as a mounted secret file, never in git.
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_API_MODEL: str = "text-embedding-3-small"
    # How long each vector is (at most 2,000, the search index's limit). With
    # EMBEDDING_API_SEND_DIMENSION the service is asked for this size (OpenAI's
    # text-embedding-3 and Google's models can shorten theirs); turn that off for a
    # service that refuses the "dimensions" field and set its own size here.
    EMBEDDING_API_DIMENSION: int = 1536
    EMBEDDING_API_SEND_DIMENSION: bool = True
    # The name authors are shown for where their writing goes ("OpenAI"); the host if empty.
    EMBEDDING_API_NAME: str = ""
    EMBEDDING_API_TIMEOUT_SECONDS: float = 30.0
    EMBEDDING_GEMMA_MODEL: str = "google/embeddinggemma-2"
    # An exact Hugging Face commit; the model is never loaded from a moving branch.
    EMBEDDING_GEMMA_REVISION: str = ""
    # Matryoshka size: 768, 512, 256 or 128 (512 is expected to be enough; measure first).
    EMBEDDING_GEMMA_DIMENSION: int = 512
    # "auto" picks bfloat16 on a GPU that supports it, else float32. Never float16.
    EMBEDDING_DTYPE: Literal["auto", "bfloat16", "float32"] = "auto"
    EMBEDDING_FAKE_DIMENSION: int = 768
    # Bring the search index in use up to date in the background every this many minutes
    # (CTX-012): pictures and accepted descriptions whose quick update was missed, a few
    # stories a time. 0 (the default) leaves it off; `make reindex-refresh` still works.
    SEARCH_INDEX_CATCH_UP_MINUTES: int = 0
    CUSTOM_SCHEMA_PATH: str = "backend/config/custom_schema.json"
    ENABLE_DEBUG_ARTIFACTS: bool = False
    ARTIFACT_STORE_DIR: str = "backend/debug"
    OBJECT_STORE_DIR: str = "data/objects"

    # Picture processing (AST-003/AST-006, OPS-007). Every picture is decoded once in a
    # separate worker process held to these limits, and turned into smaller copies.
    MEDIA_PROCESSING_ENABLED: bool = True
    # Pixel and side ceilings checked from the file's header before any decoding.
    MEDIA_MAX_PIXELS: int = 40_000_000
    MEDIA_MAX_DIMENSION: int = 12_000
    # The worker's address-space and CPU limits, and how long the parent waits for it.
    MEDIA_WORKER_MEMORY_BYTES: int = 1024 * 1024 * 1024
    MEDIA_WORKER_CPU_SECONDS: int = 20
    MEDIA_WORKER_TIMEOUT_SECONDS: float = 30.0
    # The worker's sandbox beyond those limits (Landlock, seccomp, its own network):
    # "best" uses what this machine has, "require" prepares no picture without
    # Landlock and seccomp, "off" applies none. See plan/runbooks/media-processing.md.
    MEDIA_WORKER_SANDBOX: Literal["best", "require", "off"] = "best"
    # How many pictures are decoded at once in one API/worker process.
    MEDIA_WORKER_CONCURRENCY: int = 2
    # Everything a workspace stores (pictures and their smaller copies); 0 means no limit.
    MEDIA_WORKSPACE_QUOTA_BYTES: int = 2 * 1024 * 1024 * 1024
    # A stored file with no picture record is removed only after this long, so an
    # upload still on its way is never mistaken for one left behind.
    MEDIA_ORPHAN_GRACE_HOURS: int = 24

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
    # A signed-in person who owns no workspace may create one for themselves. Who
    # may sign in at all is decided by the identity provider, not here.
    ALLOW_PERSONAL_WORKSPACE: bool = True

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
    # How long a request may run on an author's own computer before the site stops waiting
    # (SCN-002A). Local models can be slow; ten minutes unless the operator says otherwise.
    CONNECTOR_ANSWER_SECONDS: int = 600

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def api_docs_enabled(self) -> bool:
        """Docs describe the whole API surface, so a hosted or production site hides them."""
        if self.API_DOCS_ENABLED is not None:
            return self.API_DOCS_ENABLED
        return not (self.is_hosted or self.is_production)

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
        """``CREDENTIAL_PREVIOUS_KEKS`` as {version: key}. An entry that is not
        ``version:key`` with a whole positive version and a key, or that lists a version
        twice with different keys, is refused (``ValueError``) rather than dropped: a
        dropped key leaves every stored key on that version unopenable. The message names
        the entry's place in the list (1 for the first), never what it holds."""
        keys: dict[int, str] = {}
        for position, pair in enumerate(self.CREDENTIAL_PREVIOUS_KEKS.split(","), start=1):
            entry = pair.strip()
            if not entry:
                continue  # an empty place, such as a trailing comma
            version, colon, kek = entry.partition(":")
            version, kek = version.strip(), kek.strip()
            if not colon or not version.isdigit() or int(version) < 1 or not kek:
                raise ValueError(
                    f"Entry {position} of CREDENTIAL_PREVIOUS_KEKS is not in the form "
                    "version:key (a whole number from 1, a colon, then the key)."
                )
            if keys.get(int(version), kek) != kek:
                raise ValueError(
                    f"Entry {position} of CREDENTIAL_PREVIOUS_KEKS lists version {int(version)} "
                    "again with a different key. Give each older key its own version."
                )
            keys[int(version)] = kek
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
    def _validate_embedding_service(self) -> "Settings":
        # Said at start-up rather than as a failed search later.
        if not 1 <= self.EMBEDDING_API_DIMENSION <= 2000:
            raise ValueError("EMBEDDING_API_DIMENSION must be between 1 and 2000")
        if self.EMBEDDING_API_TIMEOUT_SECONDS <= 0:
            raise ValueError("EMBEDDING_API_TIMEOUT_SECONDS must be more than 0")
        return self

    @model_validator(mode="after")
    def _validate_model_capabilities(self) -> "Settings":
        # Read at start-up, so a mistake in the operator's table is said at once (CTX-013).
        from backend.domain.model_capabilities import parse_operator_table

        parse_operator_table(self.AI_MODEL_CAPABILITIES)
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
