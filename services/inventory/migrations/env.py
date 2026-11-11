"""Alembic environment: how migrations connect to inventory_db.

Alembic itself is synchronous, but our driver (asyncpg) is async. The standard
recipe: open an async connection, then hand Alembic a sync view of it via run_sync.
"""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.logging_config import configure_logging

config = context.config

# Migrations run in the container before the app starts (docker-entrypoint.sh), so
# their logs ("Running upgrade 0001 -> 0002") go to the same JSON stdout as the app.
# Code that runs migrations itself (our tests) can opt out by setting
# config.attributes["configure_logger"] = False — a recipe from the Alembic docs.
if config.attributes.get("configure_logger", True):
    configure_logging(settings.log_level, settings.service_name)

# We write migrations by hand (no ORM models to autogenerate from), so no metadata.
target_metadata = None


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of running it."""
    context.configure(url=settings.database_url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
