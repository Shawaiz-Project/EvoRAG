from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, StringConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

NonEmpty = Annotated[str, StringConstraints(min_length=1)]
Port = Annotated[int, Field(ge=1, le=65535)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_user: NonEmpty
    postgres_db: NonEmpty
    postgres_password: Annotated[SecretStr, Field(min_length=1)]
    postgres_host: NonEmpty
    postgres_port: Port
    redis_host: NonEmpty
    redis_port: Port
    cors_origins: list[str] = Field(default_factory=list)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    max_remote_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=25 * 1024 * 1024)
    gemini_api_key: SecretStr | None = None
    ollama_url: str = "http://127.0.0.1:11434"
    session_cookie_secure: bool = False
    teacher_max_refinements: int = Field(default=2, ge=0, le=5)
    mlflow_tracking_uri: str | None = None
    ragas_ollama_model: str | None = None
    workflow_checkpoint_db: Path = Path(__file__).resolve().parents[3] / ".evorag-workflows.sqlite"

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )
