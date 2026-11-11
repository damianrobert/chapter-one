"""Order's side of the checkout saga: inventory's answer in, the order's outcome out.

## The picture

Order owns two **queues** (mailboxes) on the `chapter-one.events` exchange:

- `order.stock-reserved`, bound to `StockReserved`
- `order.stock-failed`, bound to `StockFailed`

(One queue per event type, as CLAUDE.md's naming rule says: `<consumer>.<event>`.)

For each message order:

1. reads the envelope and the payload (orderId, and for StockFailed a reason),
2. finishes the order: PENDING -> CONFIRMED or CANCELLED (`finish_order()` in
   app/orders.py; only a PENDING order can change),
3. publishes what happened: `OrderConfirmed` (orderId, userId, totalMinor, currency)
   or `OrderCancelled` (orderId, userId, reason), with the same correlationId
   (hard rule 7). Notification and cart will listen to these (step 4 piece 5, step 5),
4. only then **acknowledges** ("ack") the message, so RabbitMQ deletes it.

This is the same shape as inventory's consumer (services/inventory/app/consumer.py),
on purpose: once you know one, you know the other.

## Delivered twice? (hard rule 3)

RabbitMQ delivers *at least* once, so the same StockReserved can arrive twice (e.g.
order crashed after finishing the order but before the ack). The second time the
order is no longer PENDING, so nothing changes. If it already has the status this
answer asks for, order publishes the follow-up event **again**: the first one may
never have gone out (crash between saving and publishing). Sending a follow-up twice
is the lesser evil; its consumers must cope with repeats too.

If the order has the *other* final status (StockReserved for a CANCELLED order), the
two services disagree. That can't happen today (inventory answers each order the same
way every time), so we log an error and drop the message rather than guess.

## When something goes wrong

- **Can't read the message** (not JSON, wrong shape or version) or **unknown order
  id**: retrying won't help, so it's rejected without requeue and logged as an error.
- **Temporary trouble** (database or broker unreachable): wait a moment, then put the
  message back in the queue ("nack" with requeue) to be tried again.

## Publishing the follow-up

The consumer publishes through order's existing `EventPublisher` (app/events.py), the
one `POST /orders` uses, with its own connection. Receiving and sending on separate
connections is RabbitMQ's advice: if RabbitMQ slows a busy publisher down, it doesn't
also slow down the consumer.

Still "save, then publish" as two steps: if order crashes in between, the message
isn't acked and the repeat sends the event (see above). Step 5's outbox makes this
one step (hard rule 4).
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from uuid import UUID

import aio_pika
from aio_pika.abc import AbstractIncomingMessage, AbstractQueue, AbstractRobustConnection
from sqlalchemy.ext.asyncio import AsyncEngine

from app.events import EventPublisher
from app.orders import finish_order
from app.tables import CANCELLED, CONFIRMED
from shared.events import (
    ORDER_CANCELLED,
    ORDER_CONFIRMED,
    STOCK_FAILED,
    STOCK_RESERVED,
    EventEnvelope,
    OrderCancelledPayload,
    OrderConfirmedPayload,
    StockFailedPayload,
    StockReservedPayload,
    new_event,
)
from shared.messaging import declare_exchange

logger = logging.getLogger(__name__)

# <consumer-service>.<event>, per CLAUDE.md's naming rules. Queue name -> event type.
QUEUES = {
    "order.stock-reserved": STOCK_RESERVED,
    "order.stock-failed": STOCK_FAILED,
}
# How many unacked messages RabbitMQ hands us at once (per channel, so across both
# queues). Finishing orders side by side is safe: each UPDATE touches one order.
PREFETCH = 10
CONNECT_RETRY_SECONDS = 3  # pause between attempts while RabbitMQ is unreachable
REQUEUE_DELAY_SECONDS = 2  # pause before putting a message back after a temporary error
SHUTDOWN_GRACE_SECONDS = 5  # how long stop() waits for messages in progress


class UnreadableEvent(Exception):
    """The message can't be understood; retrying won't change that."""


class UnknownOrder(Exception):
    """The answer is about an order id we don't have; retrying won't change that."""


@dataclass(frozen=True)
class StockReply:
    """Inventory's answer, read from StockReserved or StockFailed."""

    event: EventEnvelope
    order_id: UUID
    status: str  # what the order should become: CONFIRMED or CANCELLED
    cancel_reason: str | None  # set when status is CANCELLED


def read_stock_reply(body: bytes) -> StockReply:
    """Parse and check a StockReserved / StockFailed message. Raises UnreadableEvent."""
    try:
        event = EventEnvelope.from_json(body)
        if event.version != 1:
            raise UnreadableEvent(f"{event.event_type} v{event.version} is not supported")
        if event.event_type == STOCK_RESERVED:
            reserved = StockReservedPayload.model_validate(event.payload)
            return StockReply(event, reserved.order_id, CONFIRMED, None)
        if event.event_type == STOCK_FAILED:
            failed = StockFailedPayload.model_validate(event.payload)
            return StockReply(event, failed.order_id, CANCELLED, failed.reason)
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        raise UnreadableEvent(str(exc)) from exc
    raise UnreadableEvent(f"expected StockReserved or StockFailed, got {event.event_type}")


async def handle_stock_reply(engine: AsyncEngine, reply: StockReply) -> EventEnvelope | None:
    """Finish the order; return the follow-up event to publish (None: publish nothing).

    Raises UnknownOrder if the order doesn't exist.
    """
    order = await finish_order(engine, reply.order_id, reply.status, reply.cancel_reason)
    log = {
        "orderId": str(reply.order_id),
        "eventType": reply.event.event_type,
        "correlationId": str(reply.event.correlation_id),
    }
    if order is None:
        raise UnknownOrder(f"order {reply.order_id} not found")
    if order.status != reply.status:
        logger.error(
            "answer contradicts the order's status, ignored",
            extra=log | {"status": order.status, "wantedStatus": reply.status},
        )
        return None

    if order.status == CONFIRMED:
        payload = OrderConfirmedPayload(
            order_id=reply.order_id,
            user_id=order.user_id,
            total_minor=order.total_minor,
            currency=order.currency,
        ).to_dict()
        follow_up = new_event(ORDER_CONFIRMED, payload, reply.event.correlation_id)
    else:
        payload = OrderCancelledPayload(
            order_id=reply.order_id, user_id=order.user_id, reason=order.cancel_reason
        ).to_dict()
        follow_up = new_event(ORDER_CANCELLED, payload, reply.event.correlation_id)

    logger.info(
        "stock answer handled",
        extra=log | {"status": order.status, "repeat": not order.changed},
    )
    return follow_up


class StockReplyConsumer:
    def __init__(self, url: str, engine: AsyncEngine, publisher: EventPublisher):
        self._url = url
        self._engine = engine
        self._publisher = publisher
        self._connection: AbstractRobustConnection | None = None
        # queue -> consumer tag (RabbitMQ's id for "we're reading from this queue")
        self._consumers: dict[AbstractQueue, str] = {}
        self._connect_task: asyncio.Task | None = None
        self._in_progress = 0

    # --- lifecycle ---

    def start(self) -> None:
        """Connect and start consuming in the background (doesn't wait)."""
        self._connect_task = asyncio.create_task(self._connect_and_consume())

    def is_consuming(self) -> bool:
        """For /health/ready."""
        return (
            len(self._consumers) == len(QUEUES)
            and self._connection is not None
            and self._connection.connected.is_set()
        )

    async def stop(self) -> None:
        if self._connect_task is not None and not self._connect_task.done():
            self._connect_task.cancel()  # still trying to connect: give up
        for queue, tag in self._consumers.items():
            try:
                await queue.cancel(tag)  # no new messages
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
                consumers = {}
                for name, event_type in QUEUES.items():
                    # durable: the queue (and the messages in it) survive a RabbitMQ restart.
                    queue = await channel.declare_queue(name, durable=True)
                    await queue.bind(exchange, routing_key=event_type)
                    consumers[queue] = await queue.consume(self._on_message)
                self._connection, self._consumers = connection, consumers
                logger.info("consuming events", extra={"queues": list(QUEUES)})
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
            reply = read_stock_reply(message.body)
        except UnreadableEvent as exc:
            logger.error(
                "unreadable event dropped",
                extra={"messageId": message.message_id, "error": str(exc)},
            )
            await message.reject(requeue=False)
            return

        try:
            follow_up = await handle_stock_reply(self._engine, reply)
            if follow_up is not None:
                # Raises BrokerUnavailable after its 1.5s budget (app/events.py).
                await self._publisher.publish(follow_up)
        except UnknownOrder as exc:
            logger.error(
                "answer for an unknown order dropped",
                extra={"eventId": str(reply.event.event_id), "error": str(exc)},
            )
            await message.reject(requeue=False)
            return
        except Exception as exc:  # database down, broker trouble, ...
            logger.warning(
                "could not handle event, will retry",
                extra={
                    "eventId": str(reply.event.event_id),
                    "correlationId": str(reply.event.correlation_id),
                    "error": repr(exc),
                },
            )
            # Pause first, so a broken database doesn't make us spin through the same
            # message many times a second.
            await asyncio.sleep(REQUEUE_DELAY_SECONDS)
            await message.nack(requeue=True)
            return

        await message.ack()
