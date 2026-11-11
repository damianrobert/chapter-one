"""Service configuration.

All config comes from environment variables (12-factor style), so the same Docker
image runs unchanged in Docker Compose and later in Kubernetes — only the env differs.
pydantic-settings maps each field to an env var of the same name (case-insensitive)
and validates the type at startup, so a bad value fails fast instead of at first use.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "order"
    port: int = 8004  # env: PORT
    log_level: str = "INFO"  # env: LOG_LEVEL

    # env: DATABASE_URL, e.g. postgresql+asyncpg://user:pass@order-db:5432/order_db
    # No default on purpose: a missing DB URL should crash at startup, not silently
    # point at some guessed host.
    database_url: str

    # env: CART_URL, e.g. http://cart:8002 — at checkout, order reads the user's cart.
    cart_url: str
    # env: INVENTORY_URL, e.g. http://inventory:8003 — order asks inventory to reserve
    # stock (step 3 only: from step 4 on this becomes an event instead).
    inventory_url: str


settings = Settings()
