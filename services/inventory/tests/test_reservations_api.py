import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.reservations import reserve_stock
from app.schemas import ReservationItem

pytestmark = pytest.mark.db

PRIDE = "11111111-1111-4111-8111-000000000001"
DRACULA = "11111111-1111-4111-8111-000000000005"
DORIAN = "11111111-1111-4111-8111-000000000006"


def quantity_of(client, book_id: str) -> int:
    return client.get(f"/stock/{book_id}").json()["quantity"]


def reserve(client, order_id, items: dict[str, int]):
    body = {"items": [{"bookId": b, "quantity": q} for b, q in items.items()]}
    return client.put(f"/reservations/{order_id}", json=body)


def test_reserving_takes_copies_out_of_stock(db_client):
    order_id = str(uuid.uuid4())

    response = reserve(db_client, order_id, {PRIDE: 3, DRACULA: 1})

    assert response.status_code == 201
    assert response.json()["orderId"] == order_id
    assert quantity_of(db_client, PRIDE) == 7
    assert quantity_of(db_client, DRACULA) == 1


def test_repeating_the_same_reservation_does_not_take_copies_twice(db_client):
    order_id = str(uuid.uuid4())
    reserve(db_client, order_id, {DRACULA: 2})

    again = reserve(db_client, order_id, {DRACULA: 2})

    assert again.status_code == 200
    assert again.json()["items"] == [{"bookId": DRACULA, "quantity": 2}]
    assert quantity_of(db_client, DRACULA) == 0


def test_same_order_with_different_items_is_a_conflict(db_client):
    order_id = str(uuid.uuid4())
    reserve(db_client, order_id, {DRACULA: 1})

    response = reserve(db_client, order_id, {DRACULA: 2})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RESERVATION_CONFLICT"
    assert quantity_of(db_client, DRACULA) == 1


def test_not_enough_stock_reserves_nothing_at_all(db_client):
    # Pride is fine, Dorian Gray has 0. All or nothing: Pride must be untouched.
    response = reserve(db_client, uuid.uuid4(), {PRIDE: 1, DORIAN: 1})

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "INSUFFICIENT_STOCK"
    assert error["details"] == [{"bookId": DORIAN, "requested": 1}]
    assert quantity_of(db_client, PRIDE) == 10


def test_book_without_stock_record_is_insufficient_stock(db_client):
    response = reserve(db_client, uuid.uuid4(), {str(uuid.uuid4()): 1})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INSUFFICIENT_STOCK"


def test_a_failed_order_stays_failed_even_after_new_stock_arrives(db_client):
    # The outcome is stored, so a repeat (e.g. the same event delivered twice) gets the
    # same answer and doesn't lock copies away for an order that was turned down.
    order_id = str(uuid.uuid4())
    assert reserve(db_client, order_id, {DORIAN: 1}).status_code == 409

    db_client.put(f"/stock/{DORIAN}", json={"quantity": 1})

    again = reserve(db_client, order_id, {DORIAN: 1})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "INSUFFICIENT_STOCK"
    assert quantity_of(db_client, DORIAN) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"items": []},
        {"items": [{"bookId": DRACULA, "quantity": 0}]},
        {"items": [{"bookId": DRACULA, "quantity": 100}]},
        {"items": [{"bookId": DRACULA, "quantity": 1}, {"bookId": DRACULA, "quantity": 1}]},
        {"items": [{"bookId": "not-a-uuid", "quantity": 1}]},
    ],
)
def test_invalid_reservation_requests_are_400(db_client, body):
    response = db_client.put(f"/reservations/{uuid.uuid4()}", json=body)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


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
