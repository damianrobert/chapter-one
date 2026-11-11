"""Cart's OrderConfirmed consumer: a confirmed order empties the user's cart.

- Reading messages: nothing else needed.
- Handling an event: real Redis (testcontainers), no RabbitMQ.
- End to end: real Redis *and* real RabbitMQ. The test plays order: it publishes
  OrderConfirmed and watches the cart disappear.
"""

import asyncio
import json
import uuid

import aio_pika
import pytest
import redis
from redis.asyncio import Redis

from app.carts import cart_key
from app.consumer import (
    QUEUE,
    OrderConfirmedConsumer,
    UnreadableEvent,
    handle_order_confirmed,
    read_order_confirmed,
)
from shared.events import ORDER_CONFIRMED, EventEnvelope, new_event
from shared.messaging import declare_exchange, to_message

USER = "aaaaaaaa-aaaa-4aaa-8aaa-000000000001"
EMMA = "11111111-1111-4111-8111-000000000001"


def order_confirmed(order_id=None, user_id=USER) -> EventEnvelope:
    payload = {
        "orderId": str(order_id or uuid.uuid4()),
        "userId": user_id,
        "totalMinor": 999,
        "currency": "EUR",
    }
    return new_event(ORDER_CONFIRMED, payload, uuid.uuid4())


# --- reading messages ---


def test_reads_a_valid_order_confirmed():
    event = order_confirmed()

    read_event, payload = read_order_confirmed(event.to_json())

    assert read_event == event
    assert str(payload.user_id) == USER


def unreadable_bodies():
    good = json.loads(order_confirmed().to_json())
    return [
        b"not json",
        json.dumps(good | {"eventType": "OrderCancelled"}).encode(),
        json.dumps(good | {"version": 2}).encode(),
        json.dumps(good | {"payload": {"orderId": str(uuid.uuid4())}}).encode(),  # no user
    ]


@pytest.mark.parametrize("body", unreadable_bodies())
def test_unreadable_messages_are_recognised(body):
    with pytest.raises(UnreadableEvent):
        read_order_confirmed(body)


def test_queue_name_follows_the_naming_rule():
    assert QUEUE == "cart.order-confirmed"


# --- handling an event (real Redis) ---


@pytest.fixture
def raw_redis(redis_url):
    """A plain Redis client to fill and inspect carts; Redis is emptied afterwards."""
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    yield client
    client.flushdb()
    client.close()


def put_in_cart(raw_redis, user_id=USER, book_id=EMMA):
    raw_redis.hset(cart_key(user_id), book_id, json.dumps({"title": "Emma", "quantity": 1}))


def handle(redis_url, event: EventEnvelope) -> None:
    async def go():
        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            _, payload = read_order_confirmed(event.to_json())
            await handle_order_confirmed(client, event, payload)
        finally:
            await client.aclose()

    asyncio.run(go())


@pytest.mark.redis
def test_order_confirmed_empties_the_users_cart(redis_url, raw_redis):
    other_user = "aaaaaaaa-aaaa-4aaa-8aaa-000000000002"
    put_in_cart(raw_redis)
    put_in_cart(raw_redis, user_id=other_user)

    handle(redis_url, order_confirmed())

    assert not raw_redis.exists(cart_key(USER))
    assert raw_redis.exists(cart_key(other_user))  # someone else's cart is untouched


@pytest.mark.redis
def test_repeat_after_a_new_cart_leaves_the_new_cart_alone(redis_url, raw_redis):
    # At-least-once delivery: the same OrderConfirmed can arrive again later, maybe
    # after the user has started filling a new cart. It must not empty that.
    event = order_confirmed()
    put_in_cart(raw_redis)
    handle(redis_url, event)

    put_in_cart(raw_redis)  # the user shops again
    handle(redis_url, event)

    assert raw_redis.exists(cart_key(USER))


@pytest.mark.redis
def test_repeat_with_a_new_event_id_counts_as_the_same_order(redis_url, raw_redis):
    # Order may publish OrderConfirmed for the same order twice, with different ids.
    order_id = uuid.uuid4()
    handle(redis_url, order_confirmed(order_id))
    put_in_cart(raw_redis)

    handle(redis_url, order_confirmed(order_id))

    assert raw_redis.exists(cart_key(USER))


@pytest.mark.redis
def test_marker_expires_like_a_cart(redis_url, raw_redis):
    order_id = uuid.uuid4()

    handle(redis_url, order_confirmed(order_id))

    ttl = raw_redis.ttl(f"cart-emptied-for:{order_id}")
    assert 7 * 24 * 3600 - 10 < ttl <= 7 * 24 * 3600


# --- end to end: real RabbitMQ + real Redis ---


async def _deliver(rabbitmq_url, redis_url, messages, raw_redis, until):
    """Start the consumer, publish `messages` as order would, wait until `until()`."""
    client = Redis.from_url(redis_url, decode_responses=True)
    consumer = OrderConfirmedConsumer(rabbitmq_url, client)
    connection = await aio_pika.connect(rabbitmq_url)
    try:
        channel = await connection.channel()
        exchange = await declare_exchange(channel)
        consumer.start()
        async with asyncio.timeout(15):
            while not consumer.is_consuming():
                await asyncio.sleep(0.05)

        for message in messages:
            await exchange.publish(message, routing_key=ORDER_CONFIRMED)

        async with asyncio.timeout(10):
            while not until():
                await asyncio.sleep(0.05)
    finally:
        await consumer.stop()
        await connection.close()
        await client.aclose()


@pytest.mark.redis
@pytest.mark.rabbitmq
def test_bad_message_is_dropped_and_the_good_one_empties_the_cart(
    rabbitmq_url, redis_url, raw_redis
):
    put_in_cart(raw_redis)
    bad = aio_pika.Message(body=b"this is not an event")
    good = to_message(order_confirmed(), "order")

    asyncio.run(
        _deliver(
            rabbitmq_url,
            redis_url,
            [bad, good],
            raw_redis,
            until=lambda: not raw_redis.exists(cart_key(USER)),
        )
    )

    assert not raw_redis.exists(cart_key(USER))
