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

    OPENAI_API_KEY: str = ""
    AI_MODEL: str = "gpt-4o"

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
    CHROMA_PERSISTENCE_PATH: str = "./data/chroma"
    CUSTOM_SCHEMA_PATH: str = "backend/config/custom_schema.json"
    ENABLE_DEBUG_ARTIFACTS: bool = False
    ARTIFACT_STORE_DIR: str = "backend/debug"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def postgres_dsn(self) -> str:
        user = quote_plus(self.POSTGRES_USER)
        password = quote_plus(self.POSTGRES_PASSWORD)
        return (
            f"postgresql+psycopg://{user}:{password}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

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


settings = Settings()
