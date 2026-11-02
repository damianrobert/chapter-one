"""Cart configuration.

All config comes from environment variables (same approach as catalog and gateway),
so the same image runs in Docker Compose and later in Kubernetes.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "cart"
    port: int = 8002  # env: PORT
    log_level: str = "INFO"  # env: LOG_LEVEL

    # env: REDIS_URL, e.g. redis://cart-redis:6379/0 ("cart-redis" is the Compose
    # service name; "/0" is Redis database number 0). No default: without it the cart
    # can't do anything, so it should fail at startup rather than on every request.
    redis_url: str


settings = Settings()
