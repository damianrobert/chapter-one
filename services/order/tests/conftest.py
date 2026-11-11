import asyncio
import os
from pathlib import Path

import pytest

# Settings require DATABASE_URL. Set a dummy one before the app is imported.
# Tests that don't use the `database_url` fixture never connect to it: the engine
# connects lazily, and the health tests fake the DB ping.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
# Cart and inventory are faked in the tests (httpx2 MockTransport); these URLs are
# never really called.
os.environ.setdefault("CART_URL", "http://cart.test")
os.environ.setdefault("INVENTORY_URL", "http://inventory.test")

from alembic import command  # noqa: E402  (must come after DATABASE_URL is set)
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402

SERVICE_DIR = Path(__file__).parent.parent


@pytest.fixture(scope="session")
def database_url():
    """Start a throwaway Postgres in Docker, run our migrations on it, yield its URL.

    One container for the whole test run; it's deleted when tests finish and never
    touches docker-compose's order-db.
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


async def _empty_tables(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE order_items, orders"))
    await engine.dispose()


@pytest.fixture
def empty_database(database_url):
    """The throwaway Postgres's URL; its tables are emptied after the test."""
    yield database_url
    asyncio.run(_empty_tables(database_url))
