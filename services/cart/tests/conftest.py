import os

# Settings require REDIS_URL and CATALOG_URL. Set them before the app is imported.
# Nothing listens on port 1, so a test that really talks to this "Redis" gets a quick
# "connection refused". Tests that need a working Redis use the `cart_client` fixture
# (test_carts_api.py), which points the app at a throwaway Redis container. Catalog is
# never really called: tests swap the HTTP client for a fake.
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("CATALOG_URL", "http://catalog.test:8001")
# Same trick for RabbitMQ: the app's consumer keeps retrying in the background and
# never connects. test_consumer.py starts a real RabbitMQ where it needs one.
os.environ.setdefault("RABBITMQ_URL", "amqp://test:test@localhost:1/")

import pytest  # noqa: E402  (after the env vars, like the app imports)


@pytest.fixture(scope="session")
def redis_url():
    """One throwaway Redis for the whole test run, deleted when tests finish.

    Separate from docker-compose's cart-redis, so tests never touch your local carts.
    """
    from testcontainers.community.redis import RedisContainer

    # Same image as docker-compose.yml, so tests match what really runs.
    with RedisContainer("redis:8-alpine") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture(scope="session")
def rabbitmq_url():
    """Start a throwaway RabbitMQ in Docker for the test run; yield its amqp:// URL."""
    from testcontainers.community.rabbitmq import RabbitMqContainer

    # Same RabbitMQ version as docker-compose.yml (without the dashboard, not needed).
    with RabbitMqContainer("rabbitmq:4-alpine") as rabbitmq:
        params = rabbitmq.get_connection_params()
        yield f"amqp://{rabbitmq.username}:{rabbitmq.password}@{params.host}:{params.port}/"
