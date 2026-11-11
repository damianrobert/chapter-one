"""Tests for forwarding requests to the services behind the gateway.

Instead of running real services, the gateway's HTTP client is swapped for one with
a `MockTransport`. A MockTransport hands every outgoing request to a Python
function we write, which plays the part of catalog or cart. That way we can make
them answer anything, or fail like a crashed service would, without any network.
"""

import asyncio
import time

import httpx2
import pytest

from app import proxy, routes


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch):
    # Don't really wait between retries; keeps the tests fast.
    monkeypatch.setattr(proxy, "BACKOFF_SECONDS", 0)


USER_ID = "33333333-3333-4333-8333-000000000001"
BOOK_ID = "11111111-1111-4111-8111-000000000001"


def test_get_is_forwarded_with_api_prefix_stripped_and_query_kept(fake_service):
    client, calls, set_handler = fake_service
    set_handler(lambda request: httpx2.Response(200, json={"items": [], "total": 0}))

    response = client.get("/api/books?q=austen&limit=5")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}
    [sent] = calls
    assert sent.method == "GET"
    assert str(sent.url) == "http://catalog.test:8001/books?q=austen&limit=5"


def test_path_below_books_is_forwarded(fake_service):
    client, calls, _ = fake_service

    client.get("/api/books/11111111-1111-4111-8111-000000000001")

    assert calls[0].url.path == "/books/11111111-1111-4111-8111-000000000001"


def test_catalog_errors_are_passed_through_unchanged(fake_service):
    client, _, set_handler = fake_service
    error = {"error": {"code": "BOOK_NOT_FOUND", "message": "No such book"}}
    set_handler(lambda request: httpx2.Response(404, json=error))

    response = client.get("/api/books/22222222-2222-4222-8222-000000000000")

    assert response.status_code == 404
    assert response.json() == error


def test_post_forwards_body_and_rewrites_location(fake_service, auth_headers):
    client, calls, set_handler = fake_service
    set_handler(
        lambda request: httpx2.Response(201, json={"id": "abc"}, headers={"Location": "/books/abc"})
    )

    response = client.post("/api/books", json={"title": "Emma"}, headers=auth_headers(USER_ID))

    assert response.status_code == 201
    assert response.headers["location"] == "/api/books/abc"
    [sent] = calls
    assert sent.method == "POST"
    assert sent.headers["content-type"] == "application/json"
    assert b'"title"' in sent.content


def refuse_connection(request):
    raise httpx2.ConnectError("connection refused", request=request)


def test_get_is_retried_then_503_when_catalog_is_down(fake_service):
    client, calls, set_handler = fake_service
    set_handler(refuse_connection)

    response = client.get("/api/books")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert len(calls) == proxy.MAX_ATTEMPTS  # tried 3 times before giving up


def test_get_succeeds_if_a_retry_works(fake_service):
    client, calls, set_handler = fake_service
    answers = iter([refuse_connection, lambda request: httpx2.Response(200, json=[])])
    set_handler(lambda request: next(answers)(request))

    response = client.get("/api/books")

    assert response.status_code == 200
    assert len(calls) == 2


def test_post_is_not_retried(fake_service, auth_headers):
    # A failed POST might still have reached catalog; repeating it could create the
    # same book twice. So: one attempt only.
    client, calls, set_handler = fake_service
    set_handler(refuse_connection)

    response = client.post("/api/books", json={"title": "Emma"}, headers=auth_headers(USER_ID))

    assert response.status_code == 503
    assert len(calls) == 1


def test_timeout_counts_as_unavailable(fake_service):
    client, _, set_handler = fake_service

    def time_out(request):
        raise httpx2.ReadTimeout("took too long", request=request)

    set_handler(time_out)

    response = client.get("/api/books")

    assert response.status_code == 503


def test_cart_get_goes_to_the_cart_service(fake_service, auth_headers):
    client, calls, set_handler = fake_service
    cart = {"userId": USER_ID, "items": []}
    set_handler(lambda request: httpx2.Response(200, json=cart))

    response = client.get(f"/api/carts/{USER_ID}", headers=auth_headers(USER_ID))

    assert response.status_code == 200
    assert response.json() == cart
    [sent] = calls
    assert str(sent.url) == f"http://cart.test:8002/carts/{USER_ID}"


def test_cart_put_forwards_the_body(fake_service, auth_headers):
    client, calls, _ = fake_service

    client.put(
        f"/api/carts/{USER_ID}/items/{BOOK_ID}",
        json={"quantity": 2},
        headers=auth_headers(USER_ID),
    )

    [sent] = calls
    assert sent.method == "PUT"
    assert str(sent.url) == f"http://cart.test:8002/carts/{USER_ID}/items/{BOOK_ID}"
    assert b'"quantity"' in sent.content


def test_cart_delete_passes_on_an_empty_204(fake_service, auth_headers):
    client, calls, set_handler = fake_service
    set_handler(lambda request: httpx2.Response(204))

    response = client.delete(f"/api/carts/{USER_ID}/items/{BOOK_ID}", headers=auth_headers(USER_ID))

    assert response.status_code == 204
    assert response.content == b""
    assert calls[0].method == "DELETE"


def test_503_names_the_cart_service_when_it_is_down(fake_service, auth_headers):
    client, _, set_handler = fake_service
    set_handler(refuse_connection)

    response = client.get(f"/api/carts/{USER_ID}", headers=auth_headers(USER_ID))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "cart" in response.json()["error"]["message"]


def test_real_client_has_a_timeout():
    # Hard rule 6: no call may wait forever, even one made without a budget.
    client = proxy.create_client()
    assert client.timeout.read == proxy.DEFAULT_BUDGET_SECONDS
    assert client.timeout.connect == proxy.DEFAULT_BUDGET_SECONDS


# --- time budgets ---


def hang(request):
    """A service that accepts the call but never answers within the test's budget."""

    async def answer_too_late():
        await asyncio.sleep(5)
        return httpx2.Response(200, json={})

    return answer_too_late()


def test_a_hanging_service_gets_503_once_the_budget_is_used_up(fake_service, monkeypatch):
    monkeypatch.setitem(routes.BUDGETS, "catalog", 0.2)
    client, calls, set_handler = fake_service
    set_handler(hang)

    start = time.perf_counter()
    response = client.get("/api/books")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert time.perf_counter() - start < 1  # the budget, not the 5s the service wanted
    # A hang uses up the budget on the first attempt; it isn't waited for again.
    assert len(calls) == 1


def test_each_service_gets_its_own_budget(fake_service, auth_headers):
    client, calls, _ = fake_service

    client.get("/api/books")
    client.get("/api/orders", headers=auth_headers(USER_ID))

    catalog_call, order_call = calls
    assert catalog_call.extensions["timeout"]["read"] == routes.BUDGETS["catalog"]
    assert order_call.extensions["timeout"]["read"] == routes.BUDGETS["order"]


def test_callers_wait_longer_than_the_services_they_call():
    # The budget rule from docs/decisions/0002-timeout-budgets.md, as numbers:
    # cart may spend 1.5s on catalog; order may spend 1.5s on cart + 1.5s on inventory.
    assert routes.BUDGETS["cart"] > 1.5
    assert routes.BUDGETS["order"] > 1.5 + 1.5
