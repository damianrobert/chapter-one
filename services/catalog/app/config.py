"""Service configuration.

All config comes from environment variables (12-factor style), so the same Docker
image runs unchanged in Docker Compose and later in Kubernetes — only the env differs.
pydantic-settings maps each field to an env var of the same name (case-insensitive)
and validates the type at startup, so a bad value fails fast instead of at first use.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "catalog"
    port: int = 8001  # env: PORT
    log_level: str = "INFO"  # env: LOG_LEVEL


settings = Settings()
