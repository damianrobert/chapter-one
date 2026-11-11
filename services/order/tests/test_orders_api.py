"""Tests for the order API, against a real Postgres.

Postgres runs in a throwaway Docker container (see conftest.py). Cart and inventory
are faked with httpx2's MockTransport, so each test decides what's in the cart and
what inventory answers, and can check exactly what order sent to them.
"""

import json
import uuid

import httpx2
import pytest
from fastapi.testclient import TestClient

from app import clients
from app.config import settings
from app.main import app

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


class FakeServices:
    """Plays cart and inventory. Tests change the fields to change the answers."""

    def __init__(self):
        self.cart_items = [cart_item(EMMA, "Emma", 999, 2), cart_item(DUNE, "Dune", 1499, 1)]
        self.cart_down = False
        # What inventory does: "reserve", "insufficient", "down", or "flaky" (fails
        # once with a connection error, then reserves).
        self.inventory = "reserve"
        self.reservation_calls: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "cart.test":
            return self._cart(request)
        return self._inventory(request)

    def _cart(self, request):
        if self.cart_down:
            raise httpx2.ConnectError("connection refused", request=request)
        user_id = request.url.path.removeprefix("/carts/")
        total = sum(i["lineTotalMinor"] for i in self.cart_items)
        currency = "EUR" if self.cart_items else None
        body = {
            "userId": user_id,
            "items": self.cart_items,
            "totalMinor": total,
            "currency": currency,
        }
        return httpx2.Response(200, json=body)

    def _inventory(self, request):
        self.reservation_calls.append(request)
        if self.inventory == "down" or (
            self.inventory == "flaky" and len(self.reservation_calls) == 1
        ):
            raise httpx2.ConnectError("connection refused", request=request)
        if self.inventory == "insufficient":
            error = {
                "code": "INSUFFICIENT_STOCK",
                "message": "Not enough stock",
                "details": [{"bookId": DUNE, "requested": 1}],
            }
            return httpx2.Response(409, json={"error": error})
        return httpx2.Response(201, json={})


@pytest.fixture
def fake():
    return FakeServices()


@pytest.fixture
def client(empty_database, fake, monkeypatch):
    monkeypatch.setattr(settings, "database_url", empty_database)
    monkeypatch.setattr(clients, "BACKOFF_SECONDS", 0)  # don't really wait between retries
    with TestClient(app) as test_client:
        app.state.http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(fake.handle))
        yield test_client


def place_order(client, user=USER):
    return client.post("/orders", headers={"X-User-ID": user})


# --- placing an order: happy path ---


def test_order_is_confirmed_when_inventory_reserves(client, fake):
    response = place_order(client)

    assert response.status_code == 201
    order = response.json()
    assert order["status"] == "CONFIRMED"
    assert order["cancelReason"] is None
    assert order["userId"] == USER
    assert order["totalMinor"] == 2 * 999 + 1499
    assert order["currency"] == "EUR"
    # Items are sorted by title: Dune, Emma.
    assert [(i["title"], i["unitPriceMinor"], i["quantity"]) for i in order["items"]] == [
        ("Dune", 1499, 1),
        ("Emma", 999, 2),
    ]


def test_inventory_is_asked_to_reserve_the_cart_items_under_the_order_id(client, fake):
    order = place_order(client).json()

    [call] = fake.reservation_calls
    assert call.method == "PUT"
    assert call.url.path == f"/reservations/{order['id']}"
    assert json.loads(call.content) == {
        "items": [{"bookId": EMMA, "quantity": 2}, {"bookId": DUNE, "quantity": 1}]
    }


def test_order_keeps_its_price_when_the_cart_price_changes_later(client, fake):
    order = place_order(client).json()

    fake.cart_items = [cart_item(EMMA, "Emma (new edition)", 5000, 2)]
    again = client.get(f"/orders/{order['id']}", headers={"X-User-ID": USER}).json()

    emma = next(i for i in again["items"] if i["bookId"] == EMMA)
    assert emma["title"] == "Emma"
    assert emma["unitPriceMinor"] == 999
    assert again["totalMinor"] == order["totalMinor"]


def test_inventory_call_is_retried_when_there_was_no_answer(client, fake):
    fake.inventory = "flaky"

    response = place_order(client)

    assert response.json()["status"] == "CONFIRMED"
    # Same URL both times: inventory recognises the order id, so a retry can't
    # reserve the books twice.
    assert len(fake.reservation_calls) == 2
    assert fake.reservation_calls[0].url == fake.reservation_calls[1].url


# --- placing an order: things going wrong ---


def test_order_is_cancelled_when_stock_is_short(client, fake):
    fake.inventory = "insufficient"

    response = place_order(client)

    assert response.status_code == 201
    assert response.json()["status"] == "CANCELLED"
    assert response.json()["cancelReason"] == "INSUFFICIENT_STOCK"


def test_order_stays_pending_when_inventory_is_down(client, fake):
    fake.inventory = "down"

    response = place_order(client)

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "INVENTORY_UNAVAILABLE"
    order_id = error["details"][0]["orderId"]
    assert len(fake.reservation_calls) == clients.MAX_ATTEMPTS

    stored = client.get(f"/orders/{order_id}", headers={"X-User-ID": USER}).json()
    assert stored["status"] == "PENDING"


def test_empty_cart_cannot_be_ordered(client, fake):
    fake.cart_items = []

    response = place_order(client)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CART_EMPTY"
    assert fake.reservation_calls == []
    assert list_orders(client)["total"] == 0


def test_cart_down_means_503_and_no_order(client, fake):
    fake.cart_down = True

    response = place_order(client)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CART_UNAVAILABLE"
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
