"""Redis connection for the cart service.

Why Redis for carts (and not PostgreSQL like the other services): a cart is
temporary, small and read/written on almost every click. Redis keeps data in memory,
so it's very fast, and it can delete a key automatically after a set time (a TTL) —
exactly what an abandoned cart needs. If a cart is lost, nothing important is lost:
the real record is the order, which lives in order's PostgreSQL.

Like catalog's database engine, the client is created once at startup and closed at
shutdown (see the lifespan in main.py). Creating it does NOT connect yet; the first
command opens a connection. So the cart starts even while Redis is down, and
/health/ready reports that.
"""

from redis.asyncio import Redis

REDIS_TIMEOUT_SECONDS = 2


def create_client(redis_url: str) -> Redis:
    return Redis.from_url(
        redis_url,
        # Never wait forever: give up connecting after 2s...
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        # ...and give up waiting for an answer to a command after 2s.
        socket_timeout=REDIS_TIMEOUT_SECONDS,
        # Return str instead of bytes, so we don't have to .decode() every value.
        decode_responses=True,
    )


async def ping_redis(client: Redis) -> None:
    """Raise if Redis doesn't answer PING within the timeout."""
    await client.ping()
