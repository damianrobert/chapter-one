"""Cart's part of the checkout saga: when an order is confirmed, empty that user's cart.

## The picture

Cart owns one **queue** (mailbox), `cart.order-confirmed`, bound to the
`chapter-one.events` exchange for `OrderConfirmed`. For each message cart:

1. reads the envelope and the payload (orderId, userId, ...),
2. empties the user's cart, once per order (`empty_cart_for_order()` in app/carts.py),
3. **acknowledges** ("ack") the message, so RabbitMQ deletes it.

Cart only listens; it sends no events back. Order doesn't know cart is listening: it
announces "order confirmed" and whoever cares reacts. That's why this needed no change
in order.

Same shape as the consumers in inventory and order (app/consumer.py there).

## Delivered twice? (hard rule 3)

RabbitMQ delivers at least once. Emptying uses a marker per order id in Redis, set in
the same step as the delete, so a repeat of the same OrderConfirmed changes nothing,
even if the user has started a new cart in the meantime.

The marker is keyed by **orderId**, not eventId: order may publish OrderConfirmed for
the same order again with a new eventId (when inventory's answer reaches it twice).
Both copies mean the same thing, so they must count as one.

## When something goes wrong

- **Can't read the message** (not JSON, wrong shape or version): rejected without
  requeue and logged as an error; retrying won't help.
- **Redis unreachable**: wait a moment, put the message back in the queue ("nack"
  with requeue). The cart is emptied once Redis is back.
"""

import asyncio
import logging
import time

import aio_pika
from aio_pika.abc import AbstractIncomingMessage, AbstractQueue, AbstractRobustConnection
from redis.asyncio import Redis

from app.carts import empty_cart_for_order
from shared.events import ORDER_CONFIRMED, EventEnvelope, OrderConfirmedPayload
from shared.messaging import declare_exchange

logger = logging.getLogger(__name__)

# <consumer-service>.<event>, per CLAUDE.md's naming rules.
QUEUE = "cart.order-confirmed"
PREFETCH = 10  # unacked messages RabbitMQ hands us at once
CONNECT_RETRY_SECONDS = 3  # pause between attempts while RabbitMQ is unreachable
REQUEUE_DELAY_SECONDS = 2  # pause before putting a message back after a temporary error
SHUTDOWN_GRACE_SECONDS = 5  # how long stop() waits for messages in progress


class UnreadableEvent(Exception):
    """The message can't be understood; retrying won't change that."""


def read_order_confirmed(body: bytes) -> tuple[EventEnvelope, OrderConfirmedPayload]:
    """Parse and check an OrderConfirmed message. Raises UnreadableEvent."""
    try:
        event = EventEnvelope.from_json(body)
        if event.event_type != ORDER_CONFIRMED or event.version != 1:
            raise UnreadableEvent(
                f"expected OrderConfirmed v1, got {event.event_type} v{event.version}"
            )
        payload = OrderConfirmedPayload.model_validate(event.payload)
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        raise UnreadableEvent(str(exc)) from exc
    return event, payload


async def handle_order_confirmed(
    redis: Redis, event: EventEnvelope, payload: OrderConfirmedPayload
) -> None:
    emptied = await empty_cart_for_order(redis, payload.user_id, payload.order_id)
    logger.info(
        "cart emptied after order" if emptied else "order already handled, cart left alone",
        extra={
            "orderId": str(payload.order_id),
            "userId": str(payload.user_id),
            "repeat": not emptied,
            "correlationId": str(event.correlation_id),
        },
    )


class OrderConfirmedConsumer:
    def __init__(self, url: str, redis: Redis):
        self._url = url
        self._redis = redis
        self._connection: AbstractRobustConnection | None = None
        self._queue: AbstractQueue | None = None
        self._consumer_tag: str | None = None
        self._connect_task: asyncio.Task | None = None
        self._in_progress = 0

    # --- lifecycle ---

    def start(self) -> None:
        """Connect and start consuming in the background (doesn't wait)."""
        self._connect_task = asyncio.create_task(self._connect_and_consume())

    def is_consuming(self) -> bool:
        """For /health/ready."""
        return (
            self._consumer_tag is not None
            and self._connection is not None
            and self._connection.connected.is_set()
        )

    async def stop(self) -> None:
        if self._connect_task is not None and not self._connect_task.done():
            self._connect_task.cancel()  # still trying to connect: give up
        if self._queue is not None and self._consumer_tag is not None:
            try:
                await self._queue.cancel(self._consumer_tag)  # no new messages
            except Exception:
                logger.warning("could not cancel consumer cleanly", exc_info=True)
        deadline = time.monotonic() + SHUTDOWN_GRACE_SECONDS
        while self._in_progress and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if self._connection is not None:
            await self._connection.close()

    async def _connect_and_consume(self) -> None:
        while True:
            connection = None
            try:
                connection = await aio_pika.connect_robust(self._url)
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=PREFETCH)
                exchange = await declare_exchange(channel)
                # durable: the queue (and the messages in it) survive a RabbitMQ restart.
                queue = await channel.declare_queue(QUEUE, durable=True)
                await queue.bind(exchange, routing_key=ORDER_CONFIRMED)
                self._connection, self._queue = connection, queue
                self._consumer_tag = await queue.consume(self._on_message)
                logger.info("consuming events", extra={"queue": QUEUE})
                return
            except asyncio.CancelledError:
                if connection is not None:
                    await connection.close()
                raise
            except Exception as exc:
                logger.warning(
                    "RabbitMQ unreachable, retrying",
                    extra={"error": repr(exc), "retryInSeconds": CONNECT_RETRY_SECONDS},
                )
                if connection is not None:
                    await connection.close()
                await asyncio.sleep(CONNECT_RETRY_SECONDS)

    # --- handling one message ---

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        self._in_progress += 1
        try:
            await self._handle(message)
        finally:
            self._in_progress -= 1

    async def _handle(self, message: AbstractIncomingMessage) -> None:
        try:
            event, payload = read_order_confirmed(message.body)
        except UnreadableEvent as exc:
            logger.error(
                "unreadable event dropped",
                extra={"messageId": message.message_id, "error": str(exc)},
            )
            await message.reject(requeue=False)
            return

        try:
            await handle_order_confirmed(self._redis, event, payload)
        except Exception as exc:  # Redis down or too slow, ...
            logger.warning(
                "could not handle event, will retry",
                extra={
                    "eventId": str(event.event_id),
                    "correlationId": str(event.correlation_id),
                    "error": repr(exc),
                },
            )
            # Pause first, so a broken Redis doesn't make us spin through the same
            # message many times a second.
            await asyncio.sleep(REQUEUE_DELAY_SECONDS)
            await message.nack(requeue=True)
            return

        await message.ack()
