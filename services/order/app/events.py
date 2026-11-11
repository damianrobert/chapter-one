"""Publishing events to RabbitMQ.

## The picture

Order doesn't send events *to* inventory. It hands them to RabbitMQ's exchange
`chapter-one.events`, a sorting office. Each consumer (inventory, later notification
and cart) owns a **queue**, a mailbox, and tells the exchange which event types it
wants ("bind"). The exchange copies every event into each mailbox that asked for it.

So order doesn't know or care who listens. Adding a new consumer later needs no change
here.

- **Routing key = event type** (e.g. `OrderCreated`). Queues bind to the event types
  they want. The exchange is a *topic* exchange, so a queue could also bind to a pattern
  (`#` = everything, handy for a debugging queue). For now every binding is an exact
  event name.
- **Order declares only the exchange, never the queues.** A queue belongs to its
  consumer, which declares it (piece 3 for inventory). Until then, RabbitMQ has nowhere
  to put `OrderCreated` and **drops it**. We publish with `mandatory=True`, so RabbitMQ
  sends such a message back instead of dropping it silently, and we log a warning
  ("event not delivered to any queue"). That's expected while we build step 4.

## Making sure an event isn't silently lost on the way

- **Publisher confirms:** `publish()` waits until RabbitMQ says "got it". Without
  this, the message could still be sitting in our network buffer when we answer the
  client "accepted".
- **Durable exchange + persistent messages:** they survive a RabbitMQ restart once
  they are in a (durable) queue.
- **A time budget** (1.5s, like order's HTTP calls; ADR 0002): never wait forever for
  a broker that's down or hung (hard rule 6's idea, applied to the broker).

## What's still missing (on purpose, until roadmap step 5)

Order first saves the order, then publishes, as two separate steps. If the broker is
down in between, or the process dies, the order is saved but the event is never sent,
and the order stays PENDING forever. Step 5's **outbox pattern** fixes exactly this
(hard rule 4).

## Connecting lazily

We connect on the first publish (or readiness check), not at startup. That way order
starts even while RabbitMQ is down and reports it on /health/ready, the same as with
the database. Once connected, aio-pika's *robust* connection reconnects by itself if
RabbitMQ restarts.
"""

import asyncio
import logging

import aio_pika
from aio_pika.abc import AbstractExchange, AbstractRobustConnection

from shared.events import EXCHANGE, EventEnvelope

logger = logging.getLogger(__name__)

BUDGET_SECONDS = 1.5  # connect (if needed) + publish + broker's confirm, all together


class BrokerUnavailable(Exception):
    """RabbitMQ didn't take the event (down, unreachable or too slow)."""


class EventPublisher:
    def __init__(self, url: str, app_id: str):
        self._url = url
        self._app_id = app_id  # who sent it; shown in the RabbitMQ dashboard
        self._connection: AbstractRobustConnection | None = None
        self._exchange: AbstractExchange | None = None
        # Two requests arriving together must not both open a connection.
        self._lock = asyncio.Lock()

    async def publish(self, event: EventEnvelope) -> None:
        """Hand one event to RabbitMQ and wait for its confirm. Raises BrokerUnavailable."""
        try:
            async with asyncio.timeout(BUDGET_SECONDS):
                exchange = await self._get_exchange()
                await exchange.publish(
                    self._to_message(event), routing_key=event.event_type, mandatory=True
                )
        except Exception as exc:  # timeout, connection refused, channel closed, ...
            logger.error(
                "could not publish event",
                extra={
                    "eventType": event.event_type,
                    "eventId": str(event.event_id),
                    "error": repr(exc),
                },
            )
            raise BrokerUnavailable(str(exc)) from exc
        logger.info(
            "event published",
            extra={
                "eventType": event.event_type,
                "eventId": str(event.event_id),
                "correlationId": str(event.correlation_id),
            },
        )

    async def check(self) -> None:
        """For /health/ready: raise if we can't reach RabbitMQ right now."""
        async with asyncio.timeout(BUDGET_SECONDS):
            await self._get_exchange()
        if not self._connection.connected.is_set():
            # Connected before, but RabbitMQ went away; aio-pika is reconnecting.
            raise BrokerUnavailable("reconnecting to RabbitMQ")

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()

    async def _get_exchange(self) -> AbstractExchange:
        async with self._lock:
            if self._exchange is None:
                connection = await aio_pika.connect_robust(self._url)
                try:
                    # publisher_confirms=True is aio-pika's default; spelled out because
                    # it's the point: publish() waits for RabbitMQ's "got it".
                    channel = await connection.channel(publisher_confirms=True)
                    channel.return_callbacks.add(self._on_returned)
                    # Declaring is "create it if it doesn't exist yet". Safe to repeat,
                    # and every service that uses the exchange declares it the same way.
                    self._exchange = await channel.declare_exchange(
                        EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
                    )
                except BaseException:
                    await connection.close()
                    raise
                self._connection = connection
                logger.info("connected to RabbitMQ", extra={"exchange": EXCHANGE})
            return self._exchange

    def _on_returned(self, channel, message) -> None:
        """RabbitMQ handed a message back: no queue is bound for its event type."""
        logger.warning(
            "event not delivered to any queue",
            extra={"eventType": message.type, "eventId": message.message_id},
        )

    def _to_message(self, event: EventEnvelope) -> aio_pika.Message:
        return aio_pika.Message(
            body=event.to_json(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            # Copies of envelope fields as AMQP message properties: the body is the
            # source of truth, these just make the dashboard and debugging tools nicer.
            message_id=str(event.event_id),
            type=event.event_type,
            correlation_id=str(event.correlation_id),
            timestamp=event.occurred_at,
            app_id=self._app_id,
        )
