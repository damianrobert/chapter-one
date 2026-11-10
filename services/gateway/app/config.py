"""Gateway configuration.

All config comes from environment variables, so the same Docker image runs unchanged
in Docker Compose and later in Kubernetes — only the env differs. pydantic-settings
maps each field to an env var of the same name (case-insensitive) and validates it
at startup.

The gateway has no database. Its main config is the address of each service it
forwards to.
"""

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "gateway"
    port: int = 8000  # env: PORT
    log_level: str = "INFO"  # env: LOG_LEVEL

    # env: CATALOG_URL, e.g. http://catalog:8001 ("catalog" is the Compose service
    # name; in Kubernetes it will be the catalog Service's name). No default: a
    # missing address should stop the gateway at startup, not fail on every request.
    catalog_url: str
    # env: CART_URL, e.g. http://cart:8002. Required for the same reason.
    cart_url: str

    # env: JWT_SECRET, the key that signs login tokens. No default, and it must be long
    # enough that nobody can guess it.
    jwt_secret: str = Field(min_length=32)


settings = Settings()
