"""The event publisher against a real RabbitMQ (throwaway container, see conftest.py).

The test plays a consumer: it declares its own queue, binds it to `OrderCreated` on
the exchange, and checks what arrives, just like inventory will in piece 3.
"""

import asyncio
import json
import time
import uuid

import aio_pika
import pytest

from app import events
from app.events import BrokerUnavailable, EventPublisher
from shared.events import EXCHANGE, ORDER_CREATED, new_event

pytestmark = pytest.mark.rabbitmq

CORRELATION_ID = uuid.uuid4()


def run(coro):
    return asyncio.run(coro)


async def publish_and_receive(url: str, event):
    """Bind a fresh queue to OrderCreated, publish `event`, return what the queue got."""
    publisher = EventPublisher(url, "order")
    connection = await aio_pika.connect(url)
    try:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )
        queue = await channel.declare_queue(exclusive=True)  # deleted when we disconnect
        await queue.bind(exchange, routing_key=ORDER_CREATED)

        await publisher.publish(event)

        return await queue.get(timeout=5)
    finally:
        await publisher.close()
        await connection.close()


def test_published_event_arrives_in_a_bound_queue(rabbitmq_url):
    event = new_event(ORDER_CREATED, {"orderId": "abc"}, CORRELATION_ID)

    message = run(publish_and_receive(rabbitmq_url, event))

    # The body is the envelope exactly as CLAUDE.md describes it...
    body = json.loads(message.body)
    assert body["eventId"] == str(event.event_id)
    assert body["eventType"] == "OrderCreated"
    assert body["correlationId"] == str(CORRELATION_ID)
    assert body["payload"] == {"orderId": "abc"}
    # ...and the message survives a RabbitMQ restart once it's in a durable queue.
    assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    assert message.content_type == "application/json"
    assert message.message_id == str(event.event_id)
    assert message.routing_key == "OrderCreated"
    assert message.app_id == "order"


def test_event_nobody_listens_for_is_logged(rabbitmq_url, caplog):
    async def publish_unbound():
        publisher = EventPublisher(rabbitmq_url, "order")
        try:
            # No queue is bound to this event type, so RabbitMQ hands it back.
            await publisher.publish(new_event("NobodyListens", {}, CORRELATION_ID))
            await asyncio.sleep(0.2)  # the "returned" notice can arrive just after
        finally:
            await publisher.close()

    run(publish_unbound())

    assert any(
        r.message == "event not delivered to any queue" and r.eventType == "NobodyListens"
        for r in caplog.records
    )


def test_check_succeeds_when_rabbitmq_is_up(rabbitmq_url):
    async def check():
        publisher = EventPublisher(rabbitmq_url, "order")
        try:
            await publisher.check()
        finally:
            await publisher.close()

    run(check())  # no exception


def test_publishing_without_a_broker_fails_within_the_budget(monkeypatch):
    # Nothing listens on port 1: the connection is refused.
    publisher = EventPublisher("amqp://guest:guest@localhost:1/", "order")
    event = new_event(ORDER_CREATED, {}, CORRELATION_ID)

    start = time.perf_counter()
    with pytest.raises(BrokerUnavailable):
        run(publisher.publish(event))
    assert time.perf_counter() - start < events.BUDGET_SECONDS + 0.5
