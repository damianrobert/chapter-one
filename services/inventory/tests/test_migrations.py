"""The real migrations run on a real Postgres (see the `database_url` fixture)."""

import asyncio
import uuid

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.tables import stock

pytestmark = pytest.mark.db

DRACULA = uuid.UUID("11111111-1111-4111-8111-000000000005")


async def _fetch_seeded_stock(url: str) -> dict[uuid.UUID, int]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = (await conn.execute(select(stock.c.book_id, stock.c.quantity))).all()
    await engine.dispose()
    return dict(rows)


async def _set_quantity(url: str, book_id: uuid.UUID, quantity: int) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                update(stock).where(stock.c.book_id == book_id).values(quantity=quantity)
            )
    finally:
        await engine.dispose()


def test_migrations_create_stock_with_seed_rows(database_url):
    seeded = asyncio.run(_fetch_seeded_stock(database_url))

    assert len(seeded) == 6
    assert seeded[DRACULA] == 2


def test_database_refuses_negative_stock(database_url):
    # The CHECK constraint is the last line of defence against overselling.
    with pytest.raises(IntegrityError):
        asyncio.run(_set_quantity(database_url, DRACULA, -1))
