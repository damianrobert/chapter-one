import asyncio
import os
from pathlib import Path

import pytest

# Settings require DATABASE_URL. Set a dummy one before the app is imported.
# Tests that don't use the `db_client` fixture never connect to it: the engine
# connects lazily, and the health tests fake the DB ping.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

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

    scope="session": one container for the whole test run (starting Postgres takes a
    few seconds, too slow to repeat for every test). It is deleted when tests finish.
    It's a separate container from docker-compose's catalog-db, so tests never touch
    your local data.
    """
    # Imported here so the tests that don't need a DB also run without testcontainers.
    from testcontainers.community.postgres import PostgresContainer

    # Same Postgres version as docker-compose.yml, so tests match what really runs.
    with PostgresContainer("postgres:18-alpine", driver="asyncpg") as postgres:
        url = postgres.get_connection_url()

        # Run the real migrations (same ones the container runs on startup), so the
        # test DB has the real schema and the seed books. migrations/env.py reads the
        # URL from settings, so point settings at the test DB while migrating.
        alembic_cfg = Config(str(SERVICE_DIR / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(SERVICE_DIR / "migrations"))
        # Leave logging to pytest (it captures logs itself); see migrations/env.py.
        alembic_cfg.attributes["configure_logger"] = False
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "database_url", url)
            command.upgrade(alembic_cfg, "head")

        yield url


async def _delete_books_created_by_tests(url: str) -> None:
    # Seed books have fixed ids starting 11111111-1111-4111-8111- (migration 0002).
    # Delete everything else, so each test starts from just the seed data.
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM books WHERE id::text NOT LIKE '11111111-1111-4111-8111-%'")
        )
    await engine.dispose()


@pytest.fixture
def db_client(database_url, monkeypatch):
    """A test client whose app talks to the throwaway Postgres."""
    monkeypatch.setattr(settings, "database_url", database_url)
    # Using TestClient as a context manager runs the app's lifespan, which creates
    # the engine from settings.database_url.
    with TestClient(app) as client:
        yield client
    asyncio.run(_delete_books_created_by_tests(database_url))
