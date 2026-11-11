import asyncio
import os
from pathlib import Path

import pytest

# Settings require DATABASE_URL. Set a dummy one before the app is imported.
# Tests that don't use the `database_url` fixture never connect to it: the engine
# connects lazily, and the health tests fake the DB ping.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
# Nothing listens on port 1: the app's consumer just keeps retrying in the background
# during API tests. test_consumer.py starts a real RabbitMQ.
os.environ.setdefault("RABBITMQ_URL", "amqp://test:test@localhost:1/")

from alembic import command  # noqa: E402  (must come after DATABASE_URL is set)
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

SERVICE_DIR = Path(__file__).parent.parent


@pytest.fixture(scope="session")
def database_url():
    """Start a throwaway Postgres in Docker, run our migrations on it, yield its URL.

    One container for the whole test run; it's deleted when tests finish and never
    touches docker-compose's inventory-db.
    """
    # Imported here so the tests that don't need a DB also run without testcontainers.
    from testcontainers.community.postgres import PostgresContainer

    # Same Postgres version as docker-compose.yml.
    with PostgresContainer("postgres:18-alpine", driver="asyncpg") as postgres:
        url = postgres.get_connection_url()

        alembic_cfg = Config(str(SERVICE_DIR / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(SERVICE_DIR / "migrations"))
        # Leave logging to pytest (it captures logs itself); see migrations/env.py.
        alembic_cfg.attributes["configure_logger"] = False
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "database_url", url)
            command.upgrade(alembic_cfg, "head")

        yield url


# The seed quantities from migration 0002, restored after every test.
SEED_SQL = """
    TRUNCATE reservation_items, reservations, stock;
    INSERT INTO stock (book_id, quantity) VALUES
        ('11111111-1111-4111-8111-000000000001', 10),
        ('11111111-1111-4111-8111-000000000002', 10),
        ('11111111-1111-4111-8111-000000000003', 5),
        ('11111111-1111-4111-8111-000000000004', 5),
        ('11111111-1111-4111-8111-000000000005', 2),
        ('11111111-1111-4111-8111-000000000006', 0);
"""


async def reset_database(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        # asyncpg runs one statement per execute(), so split on ";".
        for statement in filter(str.strip, SEED_SQL.split(";")):
            await conn.execute(text(statement))
    await engine.dispose()


@pytest.fixture
def db_client(database_url, monkeypatch):
    """A test client whose app talks to the throwaway Postgres, reset afterwards."""
    monkeypatch.setattr(settings, "database_url", database_url)
    with TestClient(app) as client:
        yield client
    asyncio.run(reset_database(database_url))


@pytest.fixture(scope="session")
def rabbitmq_url():
    """Start a throwaway RabbitMQ in Docker for the test run; yield its amqp:// URL."""
    from testcontainers.community.rabbitmq import RabbitMqContainer

    with RabbitMqContainer("rabbitmq:4-alpine") as rabbitmq:
        params = rabbitmq.get_connection_params()
        yield f"amqp://{rabbitmq.username}:{rabbitmq.password}@{params.host}:{params.port}/"
