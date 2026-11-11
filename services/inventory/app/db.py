"""Database connection for the inventory service.

The engine owns a pool of connections to inventory_db. It is created once at startup
and disposed at shutdown (see the lifespan in main.py). Creating the engine does NOT
connect yet — connections are opened lazily on first use, so the service can start
even while the database is still down and report that via /health/ready.
"""

import asyncio
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DB_TIMEOUT_SECONDS = 2


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        database_url,
        # Check a pooled connection is still alive before handing it out; avoids
        # errors after the DB restarted and old connections went stale.
        pool_pre_ping=True,
        # asyncpg's connect timeout: never wait forever for an unreachable DB.
        connect_args={"timeout": DB_TIMEOUT_SECONDS},
    )


async def ping_database(engine: AsyncEngine) -> None:
    """Raise if the database can't answer a trivial query within the timeout."""
    async with asyncio.timeout(DB_TIMEOUT_SECONDS):
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))


def get_engine(request: Request) -> AsyncEngine:
    # A FastAPI "dependency": endpoints declare a parameter of type `Engine` and
    # FastAPI calls this to supply the engine created at startup (main.py lifespan).
    return request.app.state.engine


Engine = Annotated[AsyncEngine, Depends(get_engine)]
