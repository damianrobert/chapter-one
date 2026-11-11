"""Inventory's OrderCreated consumer.

- Reading messages: no database needed.
- Handling an event: real Postgres (testcontainers), no RabbitMQ.
- End to end: real Postgres *and* real RabbitMQ. The test plays order: it publishes
  OrderCreated and listens for inventory's reply on its own queue.
"""

import asyncio
import json
import uuid

import aio_pika
import pytest
from conftest import reset_database  # pytest puts tests/ on the import path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.consumer import (
    QUEUE,
    OrderCreatedConsumer,
    UnreadableEvent,
    handle_order_created,
    read_order_created,
)
from shared.events import (
    ORDER_CREATED,
    STOCK_FAILED,
    STOCK_RESERVED,
    EventEnvelope,
    new_event,
)
from shared.messaging import declare_exchange, to_message

PRIDE = "11111111-1111-4111-8111-000000000001"
DRACULA = "11111111-1111-4111-8111-000000000005"  # 2 in stock
DORIAN = "11111111-1111-4111-8111-000000000006"  # 0 in stock
USER = "aaaaaaaa-aaaa-4aaa-8aaa-000000000001"


def order_created(items: dict[str, int], order_id=None, correlation_id=None) -> EventEnvelope:
    payload = {
        "orderId": str(order_id or uuid.uuid4()),
        "userId": USER,
        "items": [{"bookId": b, "qty": q} for b, q in items.items()],
    }
    return new_event(ORDER_CREATED, payload, correlation_id or uuid.uuid4())


# --- reading messages ---


def test_reads_a_valid_order_created():
    event = order_created({DRACULA: 2})

    read_event, items = read_order_created(event.to_json())

    assert read_event == event
    assert [(str(i.book_id), i.quantity) for i in items] == [(DRACULA, 2)]


def unreadable_bodies():
    good = json.loads(order_created({DRACULA: 1}).to_json())
    return [
        b"not json",
        json.dumps(good | {"eventType": "SomethingElse"}).encode(),
        json.dumps(good | {"version": 2}).encode(),
        json.dumps(good | {"payload": {"orderId": "x"}}).encode(),
        json.dumps(good | {"payload": good["payload"] | {"items": []}}).encode(),
        # Same book twice, or more than the 99 a cart allows.
        json.dumps(
            good | {"payload": good["payload"] | {"items": [{"bookId": DRACULA, "qty": 1}] * 2}}
        ).encode(),
        json.dumps(
            good | {"payload": good["payload"] | {"items": [{"bookId": DRACULA, "qty": 100}]}}
        ).encode(),
    ]


@pytest.mark.parametrize("body", unreadable_bodies())
def test_unreadable_messages_are_recognised(body):
    with pytest.raises(UnreadableEvent):
        read_order_created(body)


# --- handling an event (real Postgres) ---


@pytest.fixture
def handle(database_url):
    """Handle one event against the test database; returns the reply event."""

    def run(event: EventEnvelope) -> EventEnvelope:
        async def go():
            engine = create_async_engine(database_url)
            try:
                _, items = read_order_created(event.to_json())
                return await handle_order_created(engine, event, items)
            finally:
                await engine.dispose()

        return asyncio.run(go())

    yield run
    asyncio.run(reset_database(database_url))


def quantity(database_url, book_id) -> int:
    async def go():
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as conn:
                return await conn.scalar(
                    text("SELECT quantity FROM stock WHERE book_id = :id"), {"id": book_id}
                )
        finally:
            await engine.dispose()

    return asyncio.run(go())


@pytest.mark.db
def test_enough_stock_means_stock_reserved(handle, database_url):
    event = order_created({DRACULA: 2, PRIDE: 1})

    reply = handle(event)

    assert reply.event_type == STOCK_RESERVED
    assert reply.payload == {"orderId": event.payload["orderId"]}
    assert quantity(database_url, DRACULA) == 0
    assert quantity(database_url, PRIDE) == 9


@pytest.mark.db
def test_reply_carries_the_same_correlation_id(handle):
    correlation_id = uuid.uuid4()

    reply = handle(order_created({PRIDE: 1}, correlation_id=correlation_id))

    assert reply.correlation_id == correlation_id
    assert reply.event_id != correlation_id


@pytest.mark.db
def test_short_stock_means_stock_failed_and_nothing_taken(handle, database_url):
    event = order_created({PRIDE: 1, DORIAN: 1})

    reply = handle(event)

    assert reply.event_type == STOCK_FAILED
    assert reply.payload == {"orderId": event.payload["orderId"], "reason": "INSUFFICIENT_STOCK"}
    assert quantity(database_url, PRIDE) == 10


@pytest.mark.db
def test_same_event_twice_reserves_once_and_answers_twice(handle, database_url):
    # RabbitMQ delivers at least once: a repeat must not take the copies again, but
    # it must answer again (the first answer may never have gone out).
    event = order_created({DRACULA: 1})

    first = handle(event)
    second = handle(event)

    assert first.event_type == second.event_type == STOCK_RESERVED
    assert quantity(database_url, DRACULA) == 1


@pytest.mark.db
def test_a_failed_order_still_fails_when_repeated_after_new_stock(handle, database_url):
    event = order_created({DORIAN: 1})
    assert handle(event).event_type == STOCK_FAILED

    asyncio.run(_set_stock(database_url, DORIAN, 5))

    assert handle(event).event_type == STOCK_FAILED
    assert quantity(database_url, DORIAN) == 5


async def _set_stock(url, book_id, n):
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE stock SET quantity = :n WHERE book_id = :id"), {"n": n, "id": book_id}
        )
    await engine.dispose()


# --- end to end: real RabbitMQ + real Postgres ---


async def _round_trip(rabbitmq_url, database_url, messages: list[aio_pika.Message], replies: int):
    """Start the consumer, publish `messages` as order would, collect `replies` replies."""
    engine = create_async_engine(database_url)
    consumer = OrderCreatedConsumer(rabbitmq_url, engine)
    connection = await aio_pika.connect(rabbitmq_url)
    try:
        channel = await connection.channel()
        exchange = await declare_exchange(channel)
        # Playing order: our own queue for inventory's answers.
        answers = await channel.declare_queue(exclusive=True)
        await answers.bind(exchange, routing_key=STOCK_RESERVED)
        await answers.bind(exchange, routing_key=STOCK_FAILED)

        consumer.start()
        async with asyncio.timeout(15):
            while not consumer.is_consuming():
                await asyncio.sleep(0.05)

        for message in messages:
            await exchange.publish(message, routing_key=ORDER_CREATED)

        received = []
        async with asyncio.timeout(10):
            while len(received) < replies:
                message = await answers.get(fail=False)
                if message is None:
                    await asyncio.sleep(0.05)
                    continue
                await message.ack()
                received.append(EventEnvelope.from_json(message.body))
        return received
    finally:
        await consumer.stop()
        await connection.close()
        await engine.dispose()


@pytest.fixture
def clean_database(database_url):
    yield database_url
    asyncio.run(reset_database(database_url))


@pytest.mark.db
@pytest.mark.rabbitmq
def test_order_created_in_stock_reserved_out(rabbitmq_url, clean_database):
    event = order_created({DRACULA: 1})

    [reply] = asyncio.run(
        _round_trip(rabbitmq_url, clean_database, [to_message(event, "order")], replies=1)
    )

    assert reply.event_type == STOCK_RESERVED
    assert reply.payload["orderId"] == event.payload["orderId"]
    assert reply.correlation_id == event.correlation_id
    assert quantity(clean_database, DRACULA) == 1


@pytest.mark.db
@pytest.mark.rabbitmq
def test_unreadable_message_is_dropped_and_does_not_block_the_queue(rabbitmq_url, clean_database):
    bad = aio_pika.Message(body=b"this is not an event")
    good = order_created({DORIAN: 1})

    [reply] = asyncio.run(
        _round_trip(rabbitmq_url, clean_database, [bad, to_message(good, "order")], replies=1)
    )

    assert reply.event_type == STOCK_FAILED
    assert reply.payload["orderId"] == good.payload["orderId"]


@pytest.mark.db
@pytest.mark.rabbitmq
def test_duplicate_delivery_reserves_once(rabbitmq_url, clean_database):
    event = order_created({DRACULA: 1})
    twice = [to_message(event, "order"), to_message(event, "order")]

    replies = asyncio.run(_round_trip(rabbitmq_url, clean_database, twice, replies=2))

    assert [r.event_type for r in replies] == [STOCK_RESERVED, STOCK_RESERVED]
    assert quantity(clean_database, DRACULA) == 1


def test_queue_name_follows_the_naming_rule():
    assert QUEUE == "inventory.order-created"
