import uuid

import aio_pika

from shared.events import ORDER_CREATED, new_event
from shared.messaging import to_message


def test_message_carries_the_envelope_and_is_persistent():
    event = new_event(ORDER_CREATED, {"orderId": "abc"}, uuid.uuid4())

    message = to_message(event, "order")

    assert message.body == event.to_json()
    assert message.content_type == "application/json"
    assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    assert message.message_id == str(event.event_id)
    assert message.type == "OrderCreated"
    assert message.correlation_id == str(event.correlation_id)
    assert message.app_id == "order"
