"""Tests for the order API, against a real Postgres.

Postgres runs in a throwaway Docker container (see conftest.py). Cart is faked with
httpx2's MockTransport and RabbitMQ with FakePublisher, so each test decides what's in
the cart and whether the broker works, and can check exactly which events order sent.
(test_events.py tests the real publisher against a real RabbitMQ.)
"""

import uuid

import httpx2
import pytest
from fastapi.testclient import TestClient

from app import clients
from app.config import settings
from app.events import BrokerUnavailable
from app.main import app
from shared.events import EventEnvelope, OrderCreatedPayload

pytestmark = pytest.mark.db

USER = "aaaaaaaa-aaaa-4aaa-8aaa-000000000001"
OTHER_USER = "aaaaaaaa-aaaa-4aaa-8aaa-000000000002"
EMMA = "11111111-1111-4111-8111-000000000001"
DUNE = "11111111-1111-4111-8111-000000000002"


def cart_item(book_id, title, price_minor, quantity):
    """One line of a cart, in the shape the cart service returns."""
    return {
        "bookId": book_id,
        "title": title,
        "author": "Some Author",
        "priceMinor": price_minor,
        "currency": "EUR",
        "quantity": quantity,
        "lineTotalMinor": price_minor * quantity,
    }


class FakeCart:
    """Plays the cart service. Tests change the fields to change the answers."""

    def __init__(self):
        self.items = [cart_item(EMMA, "Emma", 999, 2), cart_item(DUNE, "Dune", 1499, 1)]
        self.down = False

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if self.down:
            raise httpx2.ConnectError("connection refused", request=request)
        user_id = request.url.path.removeprefix("/carts/")
        body = {
            "userId": user_id,
            "items": self.items,
            "totalMinor": sum(i["lineTotalMinor"] for i in self.items),
            "currency": "EUR" if self.items else None,
        }
        return httpx2.Response(200, json=body)


class FakePublisher:
    """Plays app.events.EventPublisher: remembers events instead of sending them."""

    def __init__(self):
        self.events: list[EventEnvelope] = []
        self.down = False

    async def publish(self, event: EventEnvelope) -> None:
        if self.down:
            raise BrokerUnavailable("connection refused")
        self.events.append(event)

    async def check(self) -> None:
        pass

    async def close(self) -> None:
        pass


@pytest.fixture
def cart():
    return FakeCart()


@pytest.fixture
def broker():
    return FakePublisher()


@pytest.fixture
def client(empty_database, cart, broker, monkeypatch):
    monkeypatch.setattr(settings, "database_url", empty_database)
    monkeypatch.setattr(clients, "BACKOFF_SECONDS", 0)  # don't really wait between retries
    with TestClient(app) as test_client:
        app.state.http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(cart.handle))
        app.state.publisher = broker
        yield test_client


def place_order(client, user=USER, headers=None):
    return client.post("/orders", headers={"X-User-ID": user} | (headers or {}))


# --- placing an order: happy path ---


def test_order_is_accepted_as_pending(client):
    response = place_order(client)

    # 202: accepted, not finished. Inventory hasn't said anything yet.
    assert response.status_code == 202
    order = response.json()
    assert order["status"] == "PENDING"
    assert order["cancelReason"] is None
    assert order["userId"] == USER
    assert order["totalMinor"] == 2 * 999 + 1499
    assert order["currency"] == "EUR"
    # Items are sorted by title: Dune, Emma.
    assert [(i["title"], i["unitPriceMinor"], i["quantity"]) for i in order["items"]] == [
        ("Dune", 1499, 1),
        ("Emma", 999, 2),
    ]
    # Where to check on it later.
    assert response.headers["location"] == f"/orders/{order['id']}"


def test_order_created_event_is_published(client, broker):
    order = place_order(client).json()

    [event] = broker.events
    assert event.event_type == "OrderCreated"
    assert event.version == 1
    payload = OrderCreatedPayload.model_validate(event.payload)
    assert str(payload.order_id) == order["id"]
    assert str(payload.user_id) == USER
    assert [(str(i.book_id), i.qty) for i in payload.items] == [(EMMA, 2), (DUNE, 1)]


def test_event_carries_the_requests_correlation_id(client, broker):
    correlation_id = str(uuid.uuid4())

    place_order(client, headers={"X-Correlation-ID": correlation_id})

    assert str(broker.events[0].correlation_id) == correlation_id


@pytest.mark.parametrize("headers", [{}, {"X-Correlation-ID": "not-a-uuid"}])
def test_event_gets_a_new_correlation_id_when_none_usable_was_sent(client, broker, headers):
    place_order(client, headers=headers)

    assert isinstance(broker.events[0].correlation_id, uuid.UUID)


def test_order_keeps_its_price_when_the_cart_price_changes_later(client, cart):
    order = place_order(client).json()

    cart.items = [cart_item(EMMA, "Emma (new edition)", 5000, 2)]
    again = client.get(f"/orders/{order['id']}", headers={"X-User-ID": USER}).json()

    emma = next(i for i in again["items"] if i["bookId"] == EMMA)
    assert emma["title"] == "Emma"
    assert emma["unitPriceMinor"] == 999
    assert again["totalMinor"] == order["totalMinor"]


# --- placing an order: things going wrong ---


def test_order_stays_pending_when_the_broker_is_down(client, broker):
    broker.down = True

    response = place_order(client)

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "BROKER_UNAVAILABLE"
    order_id = error["details"][0]["orderId"]

    # Saved, but nobody was told: it stays PENDING (step 5's outbox fixes this).
    stored = client.get(f"/orders/{order_id}", headers={"X-User-ID": USER}).json()
    assert stored["status"] == "PENDING"


def test_empty_cart_cannot_be_ordered(client, cart, broker):
    cart.items = []

    response = place_order(client)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CART_EMPTY"
    assert broker.events == []
    assert list_orders(client)["total"] == 0


def test_cart_down_means_503_and_no_order(client, cart, broker):
    cart.down = True

    response = place_order(client)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CART_UNAVAILABLE"
    assert broker.events == []
    assert list_orders(client)["total"] == 0


def test_missing_user_header_is_a_validation_error(client):
    response = client.post("/orders")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- reading orders ---


def list_orders(client, user=USER, **params):
    return client.get("/orders", headers={"X-User-ID": user}, params=params).json()


def test_list_shows_only_my_orders_newest_first(client):
    first = place_order(client).json()
    second = place_order(client).json()
    place_order(client, user=OTHER_USER)

    page = list_orders(client)

    assert page["total"] == 2
    assert [o["id"] for o in page["items"]] == [second["id"], first["id"]]
    assert all(o["items"] for o in page["items"])  # each order comes with its items


def test_list_pages_with_limit_and_offset(client):
    ids = [place_order(client).json()["id"] for _ in range(3)]

    page = list_orders(client, limit=1, offset=1)

    assert page["total"] == 3
    assert [o["id"] for o in page["items"]] == [ids[1]]


def test_someone_elses_order_is_not_found(client):
    order = place_order(client).json()

    response = client.get(f"/orders/{order['id']}", headers={"X-User-ID": OTHER_USER})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ORDER_NOT_FOUND"


def test_unknown_order_is_not_found(client):
    response = client.get(f"/orders/{uuid.uuid4()}", headers={"X-User-ID": USER})

    assert response.status_code == 404
