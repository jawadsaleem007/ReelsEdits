"""Application configuration.

Everything is environment-driven with no secrets in code. Defaults are the
docker-compose local values so `docker compose up` works with zero setup.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REELSEDITS_", env_file=".env", extra="ignore"
    )

    environment: Literal["local", "dev", "staging", "prod"] = "local"
    log_level: str = "INFO"
    service_name: str = "reelsedits-api"

    # --- datastores ---------------------------------------------------------
    database_url: str = "postgresql+asyncpg://reelsedits:reelsedits@localhost:5432/reelsedits"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str | None = None
    clickhouse_url: str | None = None

    db_pool_size: int = 20
    db_max_overflow: int = 10

    # --- object storage -----------------------------------------------------
    s3_bucket: str = "reelsedits-local"
    s3_region: str = "us-east-1"
    s3_endpoint_url: str | None = "http://localhost:9000"   # MinIO locally
    presign_ttl_seconds: int = 3600
    multipart_part_size: int = 8 * 1024 * 1024

    # --- orchestration ------------------------------------------------------
    temporal_host: str = "localhost:7233"
    temporal_namespace: str = "default"

    # --- auth ---------------------------------------------------------------
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    jwks_url: str | None = None
    dev_auth_bypass: bool = False

    # --- limits -------------------------------------------------------------
    idempotency_ttl_seconds: int = 86_400
    max_upload_bytes: int = 4 * 1024 * 1024 * 1024
    max_clip_duration_ms: int = 10 * 60 * 1000

    #: Global GPU-second budget per hour. A circuit breaker, not a quota.
    #: The classic failure in this category is a retry loop re-rendering a 4K
    #: job five hundred times overnight; this is cheap insurance.
    gpu_seconds_budget_per_hour: int = 100_000

    # --- policy -------------------------------------------------------------
    #: Domains we are permitted to fetch references from. Empty means
    #: upload-only, which is the safe default. See docs/18 section 7.
    fetchable_domains: list[str] = Field(default_factory=list)
    #: Hard retention for URL-fetched references. A legal commitment.
    ephemeral_reference_ttl_hours: int = 24

    # --- versions (stamped into every artefact for lineage) -----------------
    analyzer_version: str = "1.4.2"
    indexer_version: str = "1.2.0"
    matcher_version: str = "0.9.1"
    renderer_version: str = "2.1.0"

    @field_validator("dev_auth_bypass")
    @classmethod
    def _no_bypass_outside_local(cls, v: bool, info) -> bool:
        if v and info.data.get("environment") not in (None, "local"):
            raise ValueError("dev_auth_bypass may only be enabled in the local environment")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
