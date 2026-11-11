"""Reserving stock (app/reservations.py), against a real Postgres.

Since step 4 the only way in is the OrderCreated consumer (test_consumer.py); these
tests call `reserve_stock()` directly to check its rules one by one. Stock levels are
read through the HTTP API (`GET /stock/{bookId}`).
"""

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.reservations import ReservationConflict, ReservationOutcome, reserve_stock
from app.schemas import ReservationItem

pytestmark = pytest.mark.db

PRIDE = "11111111-1111-4111-8111-000000000001"
DRACULA = "11111111-1111-4111-8111-000000000005"
DORIAN = "11111111-1111-4111-8111-000000000006"


def quantity_of(client, book_id: str) -> int:
    return client.get(f"/stock/{book_id}").json()["quantity"]


@pytest.fixture
def reserve(db_client, database_url):
    """reserve(order_id, {bookId: quantity}) -> ReservationOutcome, on the test DB."""

    def run(order_id, items: dict[str, int]) -> ReservationOutcome:
        async def go():
            engine = create_async_engine(database_url)
            try:
                wanted = [ReservationItem(book_id=b, quantity=q) for b, q in items.items()]
                return await reserve_stock(engine, order_id, wanted)
            finally:
                await engine.dispose()

        return asyncio.run(go())

    return run


def test_reserving_takes_copies_out_of_stock(db_client, reserve):
    outcome = reserve(uuid.uuid4(), {PRIDE: 3, DRACULA: 1})

    assert outcome.reserved and outcome.created
    assert quantity_of(db_client, PRIDE) == 7
    assert quantity_of(db_client, DRACULA) == 1


def test_repeating_the_same_reservation_does_not_take_copies_twice(db_client, reserve):
    order_id = uuid.uuid4()
    reserve(order_id, {DRACULA: 2})

    again = reserve(order_id, {DRACULA: 2})

    assert again.reserved and not again.created
    assert [(str(i.book_id), i.quantity) for i in again.items] == [(DRACULA, 2)]
    assert quantity_of(db_client, DRACULA) == 0


def test_same_order_with_different_items_is_a_conflict(db_client, reserve):
    order_id = uuid.uuid4()
    reserve(order_id, {DRACULA: 1})

    with pytest.raises(ReservationConflict):
        reserve(order_id, {DRACULA: 2})
    assert quantity_of(db_client, DRACULA) == 1


def test_not_enough_stock_reserves_nothing_at_all(db_client, reserve):
    # Pride is fine, Dorian Gray has 0. All or nothing: Pride must be untouched.
    outcome = reserve(uuid.uuid4(), {PRIDE: 1, DORIAN: 1})

    assert not outcome.reserved
    assert outcome.failure_reason == "INSUFFICIENT_STOCK"
    assert outcome.failure_details == [{"bookId": DORIAN, "requested": 1}]
    assert quantity_of(db_client, PRIDE) == 10


def test_book_without_stock_record_is_insufficient_stock(reserve):
    outcome = reserve(uuid.uuid4(), {str(uuid.uuid4()): 1})

    assert not outcome.reserved
    assert outcome.failure_reason == "INSUFFICIENT_STOCK"


def test_a_failed_order_stays_failed_even_after_new_stock_arrives(db_client, reserve):
    # The outcome is stored, so a repeat (e.g. the same event delivered twice) gets the
    # same answer and doesn't lock copies away for an order that was turned down.
    order_id = uuid.uuid4()
    assert not reserve(order_id, {DORIAN: 1}).reserved

    db_client.put(f"/stock/{DORIAN}", json={"quantity": 1})

    again = reserve(order_id, {DORIAN: 1})
    assert not again.reserved and not again.created
    assert again.failure_reason == "INSUFFICIENT_STOCK"
    assert quantity_of(db_client, DORIAN) == 1


def test_the_http_reservation_endpoint_is_gone(db_client):
    # Removed in step 4: reservations only happen because an order was placed.
    body = {"items": [{"bookId": DRACULA, "quantity": 1}]}

    response = db_client.put(f"/reservations/{uuid.uuid4()}", json=body)

    assert response.status_code == 404


async def _many_orders_at_once(url: str, orders: int) -> list[bool]:
    engine = create_async_engine(url, pool_size=orders)
    items = [ReservationItem(book_id=uuid.UUID(DRACULA), quantity=1)]

    async def one_order() -> bool:
        outcome = await reserve_stock(engine, uuid.uuid4(), items)
        return outcome.reserved

    try:
        # gather() starts them all together, so they really overlap in the database.
        return await asyncio.gather(*(one_order() for _ in range(orders)))
    finally:
        await engine.dispose()


def test_simultaneous_orders_never_oversell(db_client, database_url):
    # Hard rule 5 in action: 8 customers want Dracula at the same moment; there are 2.
    results = asyncio.run(_many_orders_at_once(database_url, orders=8))

    assert results.count(True) == 2
    assert quantity_of(db_client, DRACULA) == 0
