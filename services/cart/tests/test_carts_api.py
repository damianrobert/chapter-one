"""Tests for the cart API, against a real Redis.

Redis runs in a throwaway Docker container (testcontainers), so these tests check
real Redis behaviour: hashes, TTLs, MULTI/EXEC. Catalog is faked with a MockTransport,
so we control which books "exist" and what they cost.
"""

import httpx2
import pytest
import redis
from fastapi.testclient import TestClient

from app import carts, catalog_client
from app.config import settings
from app.main import app

pytestmark = pytest.mark.redis

USER = "aaaaaaaa-aaaa-4aaa-8aaa-000000000001"
OTHER_USER = "aaaaaaaa-aaaa-4aaa-8aaa-000000000002"
EMMA = "11111111-1111-4111-8111-000000000001"
DUNE = "11111111-1111-4111-8111-000000000002"
DOLLAR_BOOK = "11111111-1111-4111-8111-000000000003"
MISSING = "22222222-2222-4222-8222-000000000000"


def catalog_book(book_id, title, price_minor, currency="EUR"):
    return {
        "id": book_id,
        "title": title,
        "author": "Some Author",
        "priceMinor": price_minor,
        "currency": currency,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    }


@pytest.fixture(scope="session")
def redis_url():
    """One throwaway Redis for the whole test run, deleted when tests finish.

    Separate from docker-compose's cart-redis, so tests never touch your local carts.
    """
    from testcontainers.community.redis import RedisContainer

    # Same image as docker-compose.yml, so tests match what really runs.
    with RedisContainer("redis:8-alpine") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture
def raw_redis(redis_url):
    """A plain Redis client, to look at what the API stored."""
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    yield client
    client.flushdb()  # empty Redis after each test, so tests don't affect each other
    client.close()


@pytest.fixture
def fake_catalog():
    """The books our fake catalog knows. Tests may change prices or set `down`."""
    return {
        "books": {
            EMMA: catalog_book(EMMA, "Emma", 999),
            DUNE: catalog_book(DUNE, "Dune", 1499),
            DOLLAR_BOOK: catalog_book(DOLLAR_BOOK, "Dollar Book", 500, currency="USD"),
        },
        "down": False,
    }


@pytest.fixture
def cart_client(redis_url, raw_redis, fake_catalog, monkeypatch):
    monkeypatch.setattr(settings, "redis_url", redis_url)
    monkeypatch.setattr(catalog_client, "BACKOFF_SECONDS", 0)

    def handle(request: httpx2.Request) -> httpx2.Response:
        if fake_catalog["down"]:
            raise httpx2.ConnectError("connection refused", request=request)
        book_id = request.url.path.removeprefix("/books/")
        book = fake_catalog["books"].get(book_id)
        if book is None:
            return httpx2.Response(404, json={"error": {"code": "BOOK_NOT_FOUND"}})
        return httpx2.Response(200, json=book)

    with TestClient(app) as client:
        app.state.http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
        yield client


def set_quantity(client, book_id, quantity, user=USER):
    return client.put(f"/carts/{user}/items/{book_id}", json={"quantity": quantity})


# --- reading ---


def test_unknown_cart_is_empty(cart_client):
    response = cart_client.get(f"/carts/{USER}")

    assert response.status_code == 200
    assert response.json() == {"userId": USER, "items": [], "totalMinor": 0, "currency": None}


# --- setting quantities (happy path) ---


def test_put_adds_book_with_copied_price_and_title(cart_client):
    response = set_quantity(cart_client, EMMA, 2)

    assert response.status_code == 200
    assert response.json() == {
        "userId": USER,
        "items": [
            {
                "bookId": EMMA,
                "title": "Emma",
                "author": "Some Author",
                "priceMinor": 999,
                "currency": "EUR",
                "quantity": 2,
                "lineTotalMinor": 1998,
            }
        ],
        "totalMinor": 1998,
        "currency": "EUR",
    }
    # And it's really stored: a fresh GET returns the same cart.
    assert cart_client.get(f"/carts/{USER}").json() == response.json()


def test_total_adds_up_all_items_sorted_by_title(cart_client):
    set_quantity(cart_client, EMMA, 2)  # 2 x 9.99
    set_quantity(cart_client, DUNE, 1)  # 1 x 14.99

    cart = cart_client.get(f"/carts/{USER}").json()

    assert [item["title"] for item in cart["items"]] == ["Dune", "Emma"]
    assert cart["totalMinor"] == 1998 + 1499


def test_put_twice_sets_quantity_instead_of_adding(cart_client):
    set_quantity(cart_client, EMMA, 3)
    set_quantity(cart_client, EMMA, 3)  # same request again, e.g. a retry

    [item] = cart_client.get(f"/carts/{USER}").json()["items"]
    assert item["quantity"] == 3


def test_put_refreshes_the_copied_price(cart_client, fake_catalog):
    set_quantity(cart_client, EMMA, 1)
    fake_catalog["books"][EMMA]["priceMinor"] = 1299  # catalog changes the price

    # The copy only changes when the item is set again...
    assert cart_client.get(f"/carts/{USER}").json()["items"][0]["priceMinor"] == 999
    set_quantity(cart_client, EMMA, 1)
    assert cart_client.get(f"/carts/{USER}").json()["items"][0]["priceMinor"] == 1299


def test_each_user_has_their_own_cart(cart_client):
    set_quantity(cart_client, EMMA, 1, user=USER)

    assert cart_client.get(f"/carts/{OTHER_USER}").json()["items"] == []


# --- the 7-day expiry ---


def test_cart_expires_after_7_days(cart_client, raw_redis):
    set_quantity(cart_client, EMMA, 1)

    ttl = raw_redis.ttl(f"cart:{USER}")
    # A second or two may have passed since the PUT.
    assert carts.CART_TTL_SECONDS - 5 <= ttl <= carts.CART_TTL_SECONDS


def test_every_change_restarts_the_7_days(cart_client, raw_redis):
    set_quantity(cart_client, EMMA, 1)
    set_quantity(cart_client, DUNE, 1)
    raw_redis.expire(f"cart:{USER}", 60)  # pretend most of the 7 days have passed

    cart_client.delete(f"/carts/{USER}/items/{DUNE}")

    assert raw_redis.ttl(f"cart:{USER}") > 60


# --- removing ---


def test_delete_removes_the_book(cart_client):
    set_quantity(cart_client, EMMA, 1)
    set_quantity(cart_client, DUNE, 1)

    response = cart_client.delete(f"/carts/{USER}/items/{EMMA}")

    assert response.status_code == 204
    [item] = cart_client.get(f"/carts/{USER}").json()["items"]
    assert item["bookId"] == DUNE


def test_delete_of_a_book_not_in_the_cart_is_fine(cart_client):
    response = cart_client.delete(f"/carts/{USER}/items/{EMMA}")

    assert response.status_code == 204


def test_delete_works_while_catalog_is_down(cart_client, fake_catalog):
    set_quantity(cart_client, EMMA, 1)
    fake_catalog["down"] = True

    assert cart_client.delete(f"/carts/{USER}/items/{EMMA}").status_code == 204
    assert cart_client.get(f"/carts/{USER}").status_code == 200


# --- errors ---


def test_unknown_book_is_404_and_nothing_is_stored(cart_client, raw_redis):
    response = set_quantity(cart_client, MISSING, 1)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BOOK_NOT_FOUND"
    assert not raw_redis.exists(f"cart:{USER}")


def test_catalog_down_is_503(cart_client, fake_catalog):
    fake_catalog["down"] = True

    response = set_quantity(cart_client, EMMA, 1)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CATALOG_UNAVAILABLE"


def test_book_in_another_currency_is_409(cart_client):
    set_quantity(cart_client, EMMA, 1)  # EUR

    response = set_quantity(cart_client, DOLLAR_BOOK, 1)  # USD

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CURRENCY_MISMATCH"


def test_only_book_in_cart_may_be_replaced_by_another_currency_after_removal(cart_client):
    set_quantity(cart_client, EMMA, 1)
    cart_client.delete(f"/carts/{USER}/items/{EMMA}")

    assert set_quantity(cart_client, DOLLAR_BOOK, 1).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"quantity": 0},
        {"quantity": 100},
        {"quantity": 1.5},
        {"quantity": "2"},
        {},
        {"quantity": 1, "price": 1},
    ],
)
def test_invalid_quantity_is_400(cart_client, body):
    response = cart_client.put(f"/carts/{USER}/items/{EMMA}", json=body)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_ids_must_be_uuids(cart_client):
    assert cart_client.get("/carts/not-a-uuid").status_code == 400
    assert set_quantity(cart_client, "not-a-uuid", 1).status_code == 400
