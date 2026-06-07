from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TaxFlow"
    app_env: str = "development"
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 1440
    database_url: str = "sqlite:///./taxflow.db"
    redis_url: str = "memory://"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "taxflow-documents"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    aws_region: str = "us-east-1"
    celery_task_always_eager: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @field_validator("database_url", mode="before")
    @classmethod
    def normalise_db_url(cls, v: str) -> str:
        # Render injects postgres:// or postgresql:// — SQLAlchemy 2 needs the driver explicit
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+psycopg2://", 1)
        if v.startswith("postgresql://") and "+psycopg" not in v:
            return v.replace("postgresql://", "postgresql+psycopg2://", 1)
        return v

    @field_validator("s3_endpoint_url", "s3_access_key_id", "s3_secret_access_key", mode="before")
    @classmethod
    def blank_to_none(cls, value: str | None) -> str | None:
        if value == "":
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
