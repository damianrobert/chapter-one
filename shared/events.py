"""The event envelope: the common wrapper around every event sent through RabbitMQ.

Think of it as the outside of a letter. Every letter (event) has the same envelope —
an id, what kind of letter it is, when it was written, which request it belongs to,
and a format version — and the actual message goes inside, in `payload`.

Why one shared envelope?
- Every consumer can read the outside of any event the same way, before it even knows
  what the event is about (e.g. to log `eventType` or skip an `eventId` it has already
  processed — hard rule 3).
- `version` lets a payload change over time: a breaking change bumps the version, so a
  consumer can tell old and new letters apart.

This is a *contract* between services, which is why it lives in `shared/`. What each
service *does* with an event is business logic and stays in that service.

JSON field names are camelCase (`eventId`), matching CLAUDE.md and the REST APIs;
in Python the fields are snake_case (`event_id`) as usual.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

# RabbitMQ exchange all events are published to (name from CLAUDE.md's naming rules).
EXCHANGE = "chapter-one.events"

# The event types in the checkout saga (see the events table in CLAUDE.md).
# Plain string constants so a typo becomes a NameError instead of a silent mismatch.
ORDER_CREATED = "OrderCreated"
STOCK_RESERVED = "StockReserved"
STOCK_FAILED = "StockFailed"
ORDER_CONFIRMED = "OrderConfirmed"
ORDER_CANCELLED = "OrderCancelled"


class EventEnvelope(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,  # event_id <-> "eventId" in JSON
        populate_by_name=True,  # Python code may still use event_id=...
        frozen=True,  # an event is a fact that happened; it never changes afterwards
        extra="forbid",  # unknown envelope fields are a mistake, not something to ignore
    )

    event_id: uuid.UUID
    event_type: str = Field(min_length=1)
    # AwareDatetime: a time without a timezone is refused — "12:00" alone is ambiguous
    # once services run on machines in different timezones.
    occurred_at: AwareDatetime
    correlation_id: uuid.UUID
    version: int = Field(default=1, ge=1)
    # The inside of the letter. Its shape depends on event_type (see CLAUDE.md); the
    # envelope doesn't check it — the producer builds it and the consumer validates it.
    payload: dict[str, Any]

    def to_json(self) -> bytes:
        """The bytes to put in a RabbitMQ message body."""
        return self.model_dump_json(by_alias=True).encode()

    @classmethod
    def from_json(cls, body: bytes | str) -> "EventEnvelope":
        """Read a message body back. Raises pydantic.ValidationError if it's malformed."""
        return cls.model_validate_json(body)


# --- payloads: what's inside the envelope, one class per event type ---
#
# Unlike the envelope, payloads *ignore* unknown fields (extra="ignore"). That lets a
# producer add a field later without breaking consumers that don't know it yet: adding
# is a non-breaking change. Removing or renaming a field is breaking and needs a new
# `version` (CLAUDE.md).


class Payload(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, frozen=True, extra="ignore"
    )

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict for EventEnvelope.payload (UUIDs as strings, camelCase keys)."""
        return self.model_dump(mode="json", by_alias=True)


class OrderCreatedItem(Payload):
    book_id: uuid.UUID
    qty: int = Field(ge=1)


class OrderCreatedPayload(Payload):
    """OrderCreated v1: "this order needs these books". Producer: order. Consumer: inventory."""

    order_id: uuid.UUID
    user_id: uuid.UUID
    items: list[OrderCreatedItem] = Field(min_length=1)


class StockReservedPayload(Payload):
    """StockReserved v1: "the books for this order are set aside". inventory -> order."""

    order_id: uuid.UUID


# Reasons in StockFailed. Only one so far.
INSUFFICIENT_STOCK = "INSUFFICIENT_STOCK"


class StockFailedPayload(Payload):
    """StockFailed v1: "can't reserve this order's books". inventory -> order."""

    order_id: uuid.UUID
    reason: str = Field(min_length=1)  # e.g. INSUFFICIENT_STOCK


class OrderConfirmedPayload(Payload):
    """OrderConfirmed v1: "this order is final". order -> notification, cart.

    The total is money, so integer minor units plus a currency (CLAUDE.md API rules).
    """

    order_id: uuid.UUID
    user_id: uuid.UUID
    total_minor: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)


class OrderCancelledPayload(Payload):
    """OrderCancelled v1: "this order won't happen". order -> notification."""

    order_id: uuid.UUID
    user_id: uuid.UUID
    reason: str = Field(min_length=1)  # e.g. INSUFFICIENT_STOCK


def new_event(
    event_type: str,
    payload: dict[str, Any],
    correlation_id: uuid.UUID,
    version: int = 1,
) -> EventEnvelope:
    """Make a brand-new event: fresh eventId, occurredAt = now (UTC)."""
    return EventEnvelope(
        event_id=uuid.uuid4(),
        event_type=event_type,
        occurred_at=datetime.now(UTC),
        correlation_id=correlation_id,
        version=version,
        payload=payload,
    )
