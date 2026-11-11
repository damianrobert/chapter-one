"""Order's consumer of inventory's answers (StockReserved / StockFailed).

- Reading messages: no database needed.
- Handling an answer: real Postgres (testcontainers), no RabbitMQ.
- End to end: real Postgres *and* real RabbitMQ. The test plays inventory: it
  publishes StockReserved and listens for order's OrderConfirmed on its own queue.
"""

import asyncio
import json
import uuid

import aio_pika
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.consumer import (
    QUEUES,
    StockReplyConsumer,
    UnknownOrder,
    UnreadableEvent,
    handle_stock_reply,
    read_stock_reply,
)
from app.events import EventPublisher
from app.orders import save_pending_order
from app.tables import orders
from shared.events import (
    ORDER_CANCELLED,
    ORDER_CONFIRMED,
    STOCK_FAILED,
    STOCK_RESERVED,
    EventEnvelope,
    new_event,
)
from shared.messaging import declare_exchange, to_message

USER = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-000000000001")
EMMA = "11111111-1111-4111-8111-000000000001"


def stock_reserved(order_id, correlation_id=None) -> EventEnvelope:
    return new_event(STOCK_RESERVED, {"orderId": str(order_id)}, correlation_id or uuid.uuid4())


def stock_failed(order_id, reason="INSUFFICIENT_STOCK") -> EventEnvelope:
    return new_event(STOCK_FAILED, {"orderId": str(order_id), "reason": reason}, uuid.uuid4())


# --- reading messages ---


def test_reads_stock_reserved_as_confirmed():
    order_id = uuid.uuid4()

    reply = read_stock_reply(stock_reserved(order_id).to_json())

    assert (reply.order_id, reply.status, reply.cancel_reason) == (order_id, "CONFIRMED", None)


def test_reads_stock_failed_as_cancelled_with_the_reason():
    order_id = uuid.uuid4()

    reply = read_stock_reply(stock_failed(order_id).to_json())

    assert (reply.order_id, reply.status, reply.cancel_reason) == (
        order_id,
        "CANCELLED",
        "INSUFFICIENT_STOCK",
    )


def unreadable_bodies():
    good = json.loads(stock_failed(uuid.uuid4()).to_json())
    return [
        b"not json",
        json.dumps(good | {"eventType": "SomethingElse"}).encode(),
        json.dumps(good | {"version": 2}).encode(),
        json.dumps(good | {"payload": {"orderId": "x", "reason": "R"}}).encode(),
        json.dumps(good | {"payload": {"orderId": str(uuid.uuid4())}}).encode(),  # no reason
    ]


@pytest.mark.parametrize("body", unreadable_bodies())
def test_unreadable_messages_are_recognised(body):
    with pytest.raises(UnreadableEvent):
        read_stock_reply(body)


def test_queue_names_follow_the_naming_rule():
    assert QUEUES == {"order.stock-reserved": "StockReserved", "order.stock-failed": "StockFailed"}


# --- handling an answer (real Postgres) ---


CART = {
    "currency": "EUR",
    "items": [
        {
            "bookId": EMMA,
            "title": "Emma",
            "author": "Jane Austen",
            "priceMinor": 999,
            "quantity": 2,
        }
    ],
}


def run_with_engine(url, work):
    """Run `work(engine)` against the test database and return its result."""

    async def go():
        engine = create_async_engine(url)
        try:
            return await work(engine)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def new_pending_order(url) -> uuid.UUID:
    return run_with_engine(url, lambda engine: save_pending_order(engine, USER, CART))


def stored(url, order_id) -> tuple[str, str | None]:
    async def work(engine):
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    select(orders.c.status, orders.c.cancel_reason).where(orders.c.id == order_id)
                )
            ).one()
            return tuple(row)

    return run_with_engine(url, work)


def handle(url, event: EventEnvelope) -> EventEnvelope | None:
    reply = read_stock_reply(event.to_json())
    return run_with_engine(url, lambda engine: handle_stock_reply(engine, reply))


@pytest.mark.db
def test_stock_reserved_confirms_the_order(empty_database):
    order_id = new_pending_order(empty_database)
    answer = stock_reserved(order_id)

    follow_up = handle(empty_database, answer)

    assert stored(empty_database, order_id) == ("CONFIRMED", None)
    assert follow_up.event_type == ORDER_CONFIRMED
    assert follow_up.payload == {
        "orderId": str(order_id),
        "userId": str(USER),
        "totalMinor": 2 * 999,
        "currency": "EUR",
    }
    # Same request, same correlation ID, all along the saga (hard rule 7).
    assert follow_up.correlation_id == answer.correlation_id


@pytest.mark.db
def test_stock_failed_cancels_the_order_with_the_reason(empty_database):
    order_id = new_pending_order(empty_database)

    follow_up = handle(empty_database, stock_failed(order_id))

    assert stored(empty_database, order_id) == ("CANCELLED", "INSUFFICIENT_STOCK")
    assert follow_up.event_type == ORDER_CANCELLED
    assert follow_up.payload == {
        "orderId": str(order_id),
        "userId": str(USER),
        "reason": "INSUFFICIENT_STOCK",
    }


@pytest.mark.db
def test_same_answer_twice_publishes_the_follow_up_again(empty_database):
    # At-least-once delivery: the first follow-up may never have gone out, so a repeat
    # sends it again. The order itself doesn't change a second time.
    order_id = new_pending_order(empty_database)
    answer = stock_reserved(order_id)

    first = handle(empty_database, answer)
    second = handle(empty_database, answer)

    assert first.event_type == second.event_type == ORDER_CONFIRMED
    assert first.payload == second.payload
    assert stored(empty_database, order_id) == ("CONFIRMED", None)


@pytest.mark.db
def test_contradicting_answer_changes_nothing(empty_database):
    order_id = new_pending_order(empty_database)
    handle(empty_database, stock_failed(order_id))

    follow_up = handle(empty_database, stock_reserved(order_id))

    assert follow_up is None
    assert stored(empty_database, order_id) == ("CANCELLED", "INSUFFICIENT_STOCK")


@pytest.mark.db
def test_answer_for_an_unknown_order_is_refused(empty_database):
    with pytest.raises(UnknownOrder):
        handle(empty_database, stock_reserved(uuid.uuid4()))


# --- end to end: real RabbitMQ + real Postgres ---


async def _round_trip(rabbitmq_url, database_url, messages, wanted: int):
    """Start the consumer, publish `messages` as inventory would, collect `wanted` events."""
    engine = create_async_engine(database_url)
    publisher = EventPublisher(rabbitmq_url, "order")
    consumer = StockReplyConsumer(rabbitmq_url, engine, publisher)
    connection = await aio_pika.connect(rabbitmq_url)
    try:
        channel = await connection.channel()
        exchange = await declare_exchange(channel)
        # Playing notification: our own queue for order's follow-up events.
        outcomes = await channel.declare_queue(exclusive=True)
        await outcomes.bind(exchange, routing_key=ORDER_CONFIRMED)
        await outcomes.bind(exchange, routing_key=ORDER_CANCELLED)

        consumer.start()
        async with asyncio.timeout(15):
            while not consumer.is_consuming():
                await asyncio.sleep(0.05)

        for message in messages:
            await exchange.publish(message, routing_key=message.type)

        received = []
        async with asyncio.timeout(10):
            while len(received) < wanted:
                message = await outcomes.get(fail=False)
                if message is None:
                    await asyncio.sleep(0.05)
                    continue
                await message.ack()
                received.append(EventEnvelope.from_json(message.body))
        return received
    finally:
        await consumer.stop()
        await publisher.close()
        await connection.close()
        await engine.dispose()


@pytest.mark.db
@pytest.mark.rabbitmq
def test_stock_reserved_in_order_confirmed_out(rabbitmq_url, empty_database):
    order_id = new_pending_order(empty_database)
    answer = stock_reserved(order_id)

    [outcome] = asyncio.run(
        _round_trip(rabbitmq_url, empty_database, [to_message(answer, "inventory")], wanted=1)
    )

    assert outcome.event_type == ORDER_CONFIRMED
    assert outcome.payload["orderId"] == str(order_id)
    assert outcome.correlation_id == answer.correlation_id
    assert stored(empty_database, order_id) == ("CONFIRMED", None)


@pytest.mark.db
@pytest.mark.rabbitmq
def test_bad_messages_are_dropped_and_do_not_block_the_queue(rabbitmq_url, empty_database):
    order_id = new_pending_order(empty_database)
    bad = aio_pika.Message(body=b"this is not an event", type=STOCK_FAILED)
    unknown = to_message(stock_failed(uuid.uuid4()), "inventory")
    good = to_message(stock_failed(order_id), "inventory")

    [outcome] = asyncio.run(
        _round_trip(rabbitmq_url, empty_database, [bad, unknown, good], wanted=1)
    )

    assert outcome.event_type == ORDER_CANCELLED
    assert outcome.payload["orderId"] == str(order_id)
    assert stored(empty_database, order_id) == ("CANCELLED", "INSUFFICIENT_STOCK")
