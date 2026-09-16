from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_INSECURE_DEFAULTS = {"change-me-in-production", "admin123", "super123", "secret"}


class Settings(BaseSettings):
    app_name: str = "TaxFlow"
    app_env: str = "development"
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 120
    database_url: str = "sqlite:///./taxflow.db"
    redis_url: str = "memory://"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "taxflow-documents"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    aws_region: str = "us-east-1"
    celery_task_always_eager: bool = True
    # Seed credentials — override in production via environment variables
    admin_password: str = "admin123"
    superadmin_password: str = "super123"
    # AI integrations
    openai_api_key: str | None = None
    # Database connection pool
    db_pool_size: int = 20
    db_max_overflow: int = 40
    db_pool_timeout: int = 30
    # Bootstrap data cap per company (max records returned on login)
    bootstrap_record_cap: int = 10000

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    def assert_production_secrets(self) -> None:
        """Raise at startup if insecure defaults are used in production."""
        if self.app_env != "production":
            return
        insecure = []
        if self.secret_key in _INSECURE_DEFAULTS:
            insecure.append("SECRET_KEY")
        if self.admin_password in _INSECURE_DEFAULTS:
            insecure.append("ADMIN_PASSWORD")
        if self.superadmin_password in _INSECURE_DEFAULTS:
            insecure.append("SUPERADMIN_PASSWORD")
        if insecure:
            raise RuntimeError(
                f"PRODUCTION STARTUP BLOCKED — insecure default values detected for: "
                f"{', '.join(insecure)}. Set them via environment variables."
            )
        # allow_credentials=True + a literal "*" origin makes Starlette's
        # CORSMiddleware reflect whatever Origin header the request sent —
        # effectively any site can make credentialed cross-origin requests.
        if "*" in self.cors_origin_list:
            raise RuntimeError(
                "PRODUCTION STARTUP BLOCKED — CORS_ORIGINS is set to \"*\", which combined "
                "with credentialed requests allows any site to call this API as a logged-in "
                "user. Set CORS_ORIGINS to your real frontend domain(s)."
            )

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
