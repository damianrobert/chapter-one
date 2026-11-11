"""Service configuration.

All config comes from environment variables (12-factor style), so the same Docker
image runs unchanged in Docker Compose and later in Kubernetes — only the env differs.
pydantic-settings maps each field to an env var of the same name (case-insensitive)
and validates the type at startup, so a bad value fails fast instead of at first use.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "inventory"
    port: int = 8003  # env: PORT
    log_level: str = "INFO"  # env: LOG_LEVEL

    # env: DATABASE_URL, e.g. postgresql+asyncpg://user:pass@inventory-db:5432/inventory_db
    # No default on purpose: a missing DB URL should crash at startup, not silently
    # point at some guessed host.
    database_url: str

    # env: RABBITMQ_URL, e.g. amqp://user:pass@rabbitmq:5672/ — where inventory receives
    # OrderCreated and publishes StockReserved / StockFailed (step 4).
    rabbitmq_url: str


settings = Settings()
