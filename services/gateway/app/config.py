"""Gateway configuration.

All config comes from environment variables, so the same Docker image runs unchanged
in Docker Compose and later in Kubernetes — only the env differs. pydantic-settings
maps each field to an env var of the same name (case-insensitive) and validates it
at startup.

The gateway has no database. In the next pieces it gets the addresses of the
services it forwards to (e.g. CATALOG_URL).
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "gateway"
    port: int = 8000  # env: PORT
    log_level: str = "INFO"  # env: LOG_LEVEL


settings = Settings()
