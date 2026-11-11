"""Tiny RabbitMQ helpers: how every service sets up the exchange and wraps an event.

Kept in one place so all services put events "on the wire" the same way. A service
whose exchange settings differ from another's gets an error from RabbitMQ, and a
message without `persistent` could be lost on a restart. It's plumbing, not business
logic: what a service *does* with an event stays in that service.

Needs aio-pika (services that send or receive events have it).
"""

import logging

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange

from shared.events import EXCHANGE, EventEnvelope

logger = logging.getLogger(__name__)


async def declare_exchange(channel: AbstractChannel) -> AbstractExchange:
    """The `chapter-one.events` exchange: create it if it doesn't exist yet.

    - topic: queues bind to event types by name (routing key = eventType), or a
      pattern such as `#` for "everything".
    - durable: it survives a RabbitMQ restart.

    Every service declares it with these exact settings. Declaring is safe to repeat.
    """
    return await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)


def to_message(event: EventEnvelope, app_id: str) -> aio_pika.Message:
    """Wrap an event in a RabbitMQ message. Publish it with routing_key=event.event_type."""
    return aio_pika.Message(
        body=event.to_json(),
        content_type="application/json",
        # Written to disk by RabbitMQ (once in a durable queue): survives a restart.
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        # Copies of envelope fields as message properties. The body is the source of
        # truth; these make the RabbitMQ dashboard and debugging tools more useful.
        message_id=str(event.event_id),
        type=event.event_type,
        correlation_id=str(event.correlation_id),
        timestamp=event.occurred_at,
        app_id=app_id,  # which service sent it
    )


def log_unroutable(channel, message) -> None:
    """Return callback: RabbitMQ handed back a message that no queue wanted.

    Publish with `mandatory=True` and register this with
    `channel.return_callbacks.add(log_unroutable)`. Without it, an event nobody has
    bound a queue for disappears without a trace (aio-pika doesn't log it).
    """
    logger.warning(
        "event not delivered to any queue",
        extra={"eventType": message.type, "eventId": message.message_id},
    )
