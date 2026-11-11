"""Inventory's side of the checkout saga: OrderCreated in, StockReserved/StockFailed out.

## The picture

Inventory owns a **queue** (a mailbox) called `inventory.order-created`, bound to the
`chapter-one.events` exchange for the event type `OrderCreated`. RabbitMQ puts a copy
of every OrderCreated there. Inventory takes messages out one by one and for each:

1. reads the envelope and the payload (orderId, userId, items),
2. reserves the books (app/reservations.py: all or nothing, atomic, idempotent),
3. publishes the answer: `StockReserved` or `StockFailed` (reason INSUFFICIENT_STOCK),
   with the same correlationId as the OrderCreated (hard rule 7),
4. only then **acknowledges** ("ack") the message, telling RabbitMQ "done, delete it".

The queue is durable, so messages wait there while inventory is down or busy. That is
the big change from step 3: nobody waits on the phone for inventory.

## Why ack only at the end ("at least once")

If inventory crashes anywhere before the ack, RabbitMQ hands the message out again.
So nothing is lost, but the same OrderCreated can arrive twice. That's fine because
reserving is idempotent: the second time inventory finds the stored outcome (RESERVED
or FAILED) and simply publishes the same answer again. That also covers the case
"reserved, then crashed before the answer went out": the repeat sends the answer.

## When something goes wrong

- **Can't read the message** (not JSON, wrong shape, unknown version): retrying won't
  help, so it's *rejected* without requeue and logged as an error. RabbitMQ drops
  it. (A "dead-letter queue" to keep such messages for inspection could come later.)
- **Temporary trouble** (database or broker unreachable): wait a moment, then put the
  message back in the queue ("nack" with requeue) to be tried again.

## Connecting

At startup a background task connects, retrying every few seconds until RabbitMQ
answers, so inventory starts even while RabbitMQ is down (/health/ready says so).
Once connected, aio-pika's robust connection reconnects by itself and resumes
consuming after a RabbitMQ restart.

## Shutting down (SIGTERM)

Stop taking new messages, give the ones in progress a few seconds to finish, then
close the connection. A message that didn't finish was never acked, so RabbitMQ
gives it to the next inventory instance: safe, because handling is idempotent.
"""

import asyncio
import logging
import time

import aio_pika
from aio_pika.abc import (
    AbstractExchange,
    AbstractIncomingMessage,
    AbstractQueue,
    AbstractRobustConnection,
)
from sqlalchemy.ext.asyncio import AsyncEngine

from app.reservations import ReservationConflict, reserve_stock
from app.schemas import ReservationItem, ReservationRequest
from shared.events import (
    ORDER_CREATED,
    STOCK_FAILED,
    STOCK_RESERVED,
    EventEnvelope,
    OrderCreatedPayload,
    StockFailedPayload,
    StockReservedPayload,
    new_event,
)
from shared.messaging import declare_exchange, log_unroutable, to_message

logger = logging.getLogger(__name__)

# <consumer-service>.<event>, per CLAUDE.md's naming rules.
QUEUE = "inventory.order-created"
# How many unacked messages RabbitMQ hands us at once. They're handled side by side;
# reserving is safe under concurrency (see app/reservations.py).
PREFETCH = 10
CONNECT_RETRY_SECONDS = 3  # pause between attempts while RabbitMQ is unreachable
REQUEUE_DELAY_SECONDS = 2  # pause before putting a message back after a temporary error
PUBLISH_BUDGET_SECONDS = 1.5  # publish + broker's confirm (same as order's)
SHUTDOWN_GRACE_SECONDS = 5  # how long stop() waits for messages in progress


class UnreadableEvent(Exception):
    """The message can't be understood; retrying won't change that."""


def read_order_created(body: bytes) -> tuple[EventEnvelope, list[ReservationItem]]:
    """Parse and check an OrderCreated message. Raises UnreadableEvent."""
    try:
        event = EventEnvelope.from_json(body)
        if event.event_type != ORDER_CREATED or event.version != 1:
            raise UnreadableEvent(
                f"expected OrderCreated v1, got {event.event_type} v{event.version}"
            )
        payload = OrderCreatedPayload.model_validate(event.payload)
        # Same checks as the HTTP API (quantities 1-99, each book once, ...).
        request = ReservationRequest(
            items=[ReservationItem(book_id=i.book_id, quantity=i.qty) for i in payload.items]
        )
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        raise UnreadableEvent(str(exc)) from exc
    return event, request.items


async def handle_order_created(
    engine: AsyncEngine, event: EventEnvelope, items: list[ReservationItem]
) -> EventEnvelope:
    """Reserve the order's books; return the reply event to publish."""
    order_id = OrderCreatedPayload.model_validate(event.payload).order_id
    outcome = await reserve_stock(engine, order_id, items)

    if outcome.reserved:
        payload = StockReservedPayload(order_id=order_id).to_dict()
        reply = new_event(STOCK_RESERVED, payload, event.correlation_id)
    else:
        payload = StockFailedPayload(order_id=order_id, reason=outcome.failure_reason).to_dict()
        reply = new_event(STOCK_FAILED, payload, event.correlation_id)

    logger.info(
        "order handled",
        extra={
            "orderId": str(order_id),
            "result": reply.event_type,
            "repeat": not outcome.created,  # True: delivered before, same answer again
            "correlationId": str(event.correlation_id),
        },
    )
    return reply


class OrderCreatedConsumer:
    def __init__(self, url: str, engine: AsyncEngine):
        self._url = url
        self._engine = engine
        self._connection: AbstractRobustConnection | None = None
        self._exchange: AbstractExchange | None = None
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
                channel = await connection.channel(publisher_confirms=True)
                channel.return_callbacks.add(log_unroutable)
                await channel.set_qos(prefetch_count=PREFETCH)
                self._exchange = await declare_exchange(channel)
                # durable: the queue (and the messages in it) survive a RabbitMQ restart.
                queue = await channel.declare_queue(QUEUE, durable=True)
                await queue.bind(self._exchange, routing_key=ORDER_CREATED)
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
            event, items = read_order_created(message.body)
        except UnreadableEvent as exc:
            logger.error(
                "unreadable event dropped",
                extra={"messageId": message.message_id, "error": str(exc)},
            )
            await message.reject(requeue=False)
            return

        try:
            reply = await handle_order_created(self._engine, event, items)
            await self._publish(reply)
        except ReservationConflict as exc:
            logger.error(
                "order id already used for other items, event dropped",
                extra={"eventId": str(event.event_id), "error": str(exc)},
            )
            await message.reject(requeue=False)
            return
        except Exception as exc:  # database down, broker trouble, ...
            logger.warning(
                "could not handle event, will retry",
                extra={
                    "eventId": str(event.event_id),
                    "correlationId": str(event.correlation_id),
                    "error": repr(exc),
                },
            )
            # Pause first, so a broken database doesn't make us spin through the same
            # message many times a second.
            await asyncio.sleep(REQUEUE_DELAY_SECONDS)
            await message.nack(requeue=True)
            return

        await message.ack()

    async def _publish(self, event: EventEnvelope) -> None:
        async with asyncio.timeout(PUBLISH_BUDGET_SECONDS):
            await self._exchange.publish(
                to_message(event, "inventory"), routing_key=event.event_type, mandatory=True
            )
        logger.info(
            "event published",
            extra={
                "eventType": event.event_type,
                "eventId": str(event.event_id),
                "correlationId": str(event.correlation_id),
            },
        )
