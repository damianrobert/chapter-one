"""The real migrations run on a real Postgres (see the `database_url` fixture).

These tests check that the tables exist and that the database itself refuses orders
that break the rules.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.tables import CANCELLED, PENDING, order_items, orders

pytestmark = pytest.mark.db

BOOK = uuid.UUID("11111111-1111-4111-8111-000000000001")


async def _insert_order(url: str, item_quantity: int = 1, **order_fields) -> uuid.UUID:
    """Insert one order with one item; return its (DB-generated) id."""
    values = {
        "user_id": uuid.uuid4(),
        "status": PENDING,
        "total_minor": 999,
        "currency": "EUR",
    } | order_fields
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            order_id = await conn.scalar(insert(orders).values(values).returning(orders.c.id))
            await conn.execute(
                insert(order_items).values(
                    order_id=order_id,
                    book_id=BOOK,
                    title="Pride and Prejudice",
                    author="Jane Austen",
                    unit_price_minor=999,
                    quantity=item_quantity,
                )
            )
        return order_id
    finally:
        await engine.dispose()


async def _load_items(url: str, order_id: uuid.UUID) -> list[dict]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(select(order_items).where(order_items.c.order_id == order_id))
        result = [dict(row) for row in rows.mappings()]
    await engine.dispose()
    return result


def test_an_order_with_items_can_be_stored(database_url):
    order_id = asyncio.run(_insert_order(database_url))

    [item] = asyncio.run(_load_items(database_url, order_id))
    assert item["title"] == "Pride and Prejudice"
    assert item["unit_price_minor"] == 999


@pytest.mark.parametrize(
    "bad",
    [
        {"status": "SHIPPED"},  # not one of the three statuses
        {"total_minor": -1},
        {"currency": "eur"},
        {"status": PENDING, "cancel_reason": "oops"},  # reason without being cancelled
    ],
)
def test_database_refuses_invalid_orders(database_url, bad):
    with pytest.raises(IntegrityError):
        asyncio.run(_insert_order(database_url, **bad))


def test_cancelled_order_may_have_a_reason(database_url):
    asyncio.run(_insert_order(database_url, status=CANCELLED, cancel_reason="INSUFFICIENT_STOCK"))


def test_database_refuses_zero_quantity_items(database_url):
    with pytest.raises(IntegrityError):
        asyncio.run(_insert_order(database_url, item_quantity=0))
