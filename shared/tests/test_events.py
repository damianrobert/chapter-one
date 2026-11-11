import json
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from shared.events import (
    ORDER_CREATED,
    EventEnvelope,
    OrderCreatedItem,
    OrderCreatedPayload,
    new_event,
)

CORRELATION_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


def test_new_event_fills_in_id_and_time():
    before = datetime.now(UTC)
    event = new_event(ORDER_CREATED, {"orderId": "abc"}, CORRELATION_ID)

    assert isinstance(event.event_id, uuid.UUID)
    assert before <= event.occurred_at <= datetime.now(UTC)
    assert event.event_type == "OrderCreated"
    assert event.correlation_id == CORRELATION_ID
    assert event.version == 1
    assert event.payload == {"orderId": "abc"}


def test_each_event_gets_its_own_id():
    a = new_event(ORDER_CREATED, {}, CORRELATION_ID)
    b = new_event(ORDER_CREATED, {}, CORRELATION_ID)
    assert a.event_id != b.event_id


def test_json_matches_the_envelope_in_claude_md():
    event = EventEnvelope(
        event_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
        event_type=ORDER_CREATED,
        occurred_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        correlation_id=CORRELATION_ID,
        payload={"orderId": "abc"},
    )

    assert json.loads(event.to_json()) == {
        "eventId": "11111111-1111-4111-8111-111111111111",
        "eventType": "OrderCreated",
        "occurredAt": "2026-01-01T12:00:00Z",
        "correlationId": "22222222-2222-4222-8222-222222222222",
        "version": 1,
        "payload": {"orderId": "abc"},
    }


def test_round_trip_through_json():
    event = new_event(ORDER_CREATED, {"items": [{"bookId": "x", "qty": 2}]}, CORRELATION_ID)
    assert EventEnvelope.from_json(event.to_json()) == event


@pytest.mark.parametrize(
    "change",
    [
        {"eventId": "not-a-uuid"},
        {"eventType": ""},
        {"occurredAt": "2026-01-01T12:00:00"},  # no timezone
        {"version": 0},
        {"somethingElse": 1},  # unknown envelope field
    ],
)
def test_malformed_envelopes_are_refused(change):
    good = json.loads(new_event(ORDER_CREATED, {}, CORRELATION_ID).to_json())
    with pytest.raises(ValidationError):
        EventEnvelope.from_json(json.dumps(good | change))


def test_missing_payload_is_refused():
    good = json.loads(new_event(ORDER_CREATED, {}, CORRELATION_ID).to_json())
    del good["payload"]
    with pytest.raises(ValidationError):
        EventEnvelope.from_json(json.dumps(good))


def test_events_cannot_be_changed_after_creation():
    event = new_event(ORDER_CREATED, {}, CORRELATION_ID)
    with pytest.raises(ValidationError):
        event.event_type = "Something"


# --- payloads ---

ORDER_ID = "33333333-3333-4333-8333-333333333333"
USER_ID = "44444444-4444-4444-8444-444444444444"
BOOK_ID = "11111111-1111-4111-8111-000000000001"


def test_order_created_payload_as_json():
    payload = OrderCreatedPayload(
        order_id=uuid.UUID(ORDER_ID),
        user_id=uuid.UUID(USER_ID),
        items=[OrderCreatedItem(book_id=uuid.UUID(BOOK_ID), qty=2)],
    )

    assert payload.to_dict() == {
        "orderId": ORDER_ID,
        "userId": USER_ID,
        "items": [{"bookId": BOOK_ID, "qty": 2}],
    }


def test_order_created_payload_read_back_from_an_event():
    data = {"orderId": ORDER_ID, "userId": USER_ID, "items": [{"bookId": BOOK_ID, "qty": 2}]}
    event = EventEnvelope.from_json(new_event(ORDER_CREATED, data, CORRELATION_ID).to_json())

    payload = OrderCreatedPayload.model_validate(event.payload)

    assert payload.items[0].qty == 2


def test_payloads_ignore_fields_they_dont_know_yet():
    # A producer adding a field must not break older consumers.
    data = {"orderId": ORDER_ID, "userId": USER_ID, "items": [{"bookId": BOOK_ID, "qty": 1}]}
    payload = OrderCreatedPayload.model_validate(data | {"giftWrap": True})
    assert payload.order_id == uuid.UUID(ORDER_ID)


@pytest.mark.parametrize(
    "change",
    [{"items": []}, {"items": [{"bookId": BOOK_ID, "qty": 0}]}, {"orderId": "nope"}],
)
def test_bad_order_created_payloads_are_refused(change):
    data = {"orderId": ORDER_ID, "userId": USER_ID, "items": [{"bookId": BOOK_ID, "qty": 1}]}
    with pytest.raises(ValidationError):
        OrderCreatedPayload.model_validate(data | change)
