"""Tests for forwarding requests to catalog.

Instead of running a real catalog, the gateway's HTTP client is swapped for one with
a `MockTransport`. A MockTransport hands every outgoing request to a Python
function we write, which plays the part of catalog. That way we can make "catalog"
answer anything, or fail like a crashed service would, without any network.
"""

import httpx2
import pytest
from fastapi.testclient import TestClient

from app import proxy
from app.main import app


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch):
    # Don't really wait between retries; keeps the tests fast.
    monkeypatch.setattr(proxy, "BACKOFF_SECONDS", 0)


@pytest.fixture
def fake_catalog():
    """Start the app with a fake catalog. `calls` records what the gateway sent."""
    calls: list[httpx2.Request] = []
    state = {"handler": lambda request: httpx2.Response(200, json={"ok": True})}

    def transport_handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return state["handler"](request)

    with TestClient(app) as client:
        # Replace the real client created at startup with one that talks to our fake.
        app.state.http_client = httpx2.AsyncClient(
            transport=httpx2.MockTransport(transport_handler)
        )

        def set_handler(handler):
            state["handler"] = handler

        yield client, calls, set_handler


def test_get_is_forwarded_with_api_prefix_stripped_and_query_kept(fake_catalog):
    client, calls, set_handler = fake_catalog
    set_handler(lambda request: httpx2.Response(200, json={"items": [], "total": 0}))

    response = client.get("/api/books?q=austen&limit=5")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}
    [sent] = calls
    assert sent.method == "GET"
    assert str(sent.url) == "http://catalog.test:8001/books?q=austen&limit=5"


def test_path_below_books_is_forwarded(fake_catalog):
    client, calls, _ = fake_catalog

    client.get("/api/books/11111111-1111-4111-8111-000000000001")

    assert calls[0].url.path == "/books/11111111-1111-4111-8111-000000000001"


def test_catalog_errors_are_passed_through_unchanged(fake_catalog):
    client, _, set_handler = fake_catalog
    error = {"error": {"code": "BOOK_NOT_FOUND", "message": "No such book"}}
    set_handler(lambda request: httpx2.Response(404, json=error))

    response = client.get("/api/books/22222222-2222-4222-8222-000000000000")

    assert response.status_code == 404
    assert response.json() == error


def test_post_forwards_body_and_rewrites_location(fake_catalog):
    client, calls, set_handler = fake_catalog
    set_handler(
        lambda request: httpx2.Response(201, json={"id": "abc"}, headers={"Location": "/books/abc"})
    )

    response = client.post("/api/books", json={"title": "Emma"})

    assert response.status_code == 201
    assert response.headers["location"] == "/api/books/abc"
    [sent] = calls
    assert sent.method == "POST"
    assert sent.headers["content-type"] == "application/json"
    assert b'"title"' in sent.content


def refuse_connection(request):
    raise httpx2.ConnectError("connection refused", request=request)


def test_get_is_retried_then_503_when_catalog_is_down(fake_catalog):
    client, calls, set_handler = fake_catalog
    set_handler(refuse_connection)

    response = client.get("/api/books")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert len(calls) == proxy.MAX_ATTEMPTS  # tried 3 times before giving up


def test_get_succeeds_if_a_retry_works(fake_catalog):
    client, calls, set_handler = fake_catalog
    answers = iter([refuse_connection, lambda request: httpx2.Response(200, json=[])])
    set_handler(lambda request: next(answers)(request))

    response = client.get("/api/books")

    assert response.status_code == 200
    assert len(calls) == 2


def test_post_is_not_retried(fake_catalog):
    # A failed POST might still have reached catalog; repeating it could create the
    # same book twice. So: one attempt only.
    client, calls, set_handler = fake_catalog
    set_handler(refuse_connection)

    response = client.post("/api/books", json={"title": "Emma"})

    assert response.status_code == 503
    assert len(calls) == 1


def test_timeout_counts_as_unavailable(fake_catalog):
    client, _, set_handler = fake_catalog

    def time_out(request):
        raise httpx2.ReadTimeout("took too long", request=request)

    set_handler(time_out)

    response = client.get("/api/books")

    assert response.status_code == 503


def test_real_client_has_a_timeout():
    # Hard rule 6: no call may wait forever.
    client = proxy.create_client()
    assert client.timeout.read == proxy.TIMEOUT_SECONDS
    assert client.timeout.connect == proxy.TIMEOUT_SECONDS
