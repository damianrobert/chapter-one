"""Tests for asking catalog about a book.

Catalog is played by a `MockTransport`: every request the client sends goes to a
Python function we write instead of over the network (same technique as the
gateway's tests/test_proxy.py).
"""

import asyncio
from uuid import UUID

import httpx2
import pytest

from app import catalog_client

BOOK_ID = UUID("11111111-1111-4111-8111-000000000001")
CATALOG_URL = "http://catalog.test:8001"


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch):
    monkeypatch.setattr(catalog_client, "BACKOFF_SECONDS", 0)


def get_book_with(handler):
    """Call get_book against a fake catalog; return (result, requests catalog got)."""
    calls: list[httpx2.Request] = []

    def transport_handler(request):
        calls.append(request)
        return handler(request)

    async def run():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport_handler)) as c:
            return await catalog_client.get_book(c, CATALOG_URL, BOOK_ID)

    return asyncio.run(run()), calls


def refuse_connection(request):
    raise httpx2.ConnectError("connection refused", request=request)


def test_returns_the_book_from_catalog():
    book = {"id": str(BOOK_ID), "title": "Emma", "priceMinor": 999, "currency": "EUR"}

    result, calls = get_book_with(lambda request: httpx2.Response(200, json=book))

    assert result == book
    [sent] = calls
    assert sent.method == "GET"
    assert str(sent.url) == f"{CATALOG_URL}/books/{BOOK_ID}"


def test_returns_none_when_catalog_says_404():
    result, _ = get_book_with(lambda request: httpx2.Response(404, json={"error": {}}))

    assert result is None


def test_retries_then_gives_up_when_catalog_is_down():
    calls = []

    def handler(request):
        calls.append(request)
        return refuse_connection(request)

    with pytest.raises(catalog_client.CatalogUnavailable):
        get_book_with(handler)
    assert len(calls) == catalog_client.MAX_ATTEMPTS


def test_succeeds_if_a_retry_works():
    answers = iter([refuse_connection, lambda request: httpx2.Response(200, json={"id": "x"})])

    result, calls = get_book_with(lambda request: next(answers)(request))

    assert result == {"id": "x"}
    assert len(calls) == 2


def test_catalog_server_error_counts_as_unavailable_and_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(503, json={"error": {"code": "DATABASE_UNAVAILABLE"}})

    with pytest.raises(catalog_client.CatalogUnavailable):
        get_book_with(handler)
    assert len(calls) == 1


def test_real_client_has_a_timeout():
    # Hard rule 6: no call may wait forever.
    client = catalog_client.create_client()
    assert client.timeout.connect == catalog_client.TIMEOUT_SECONDS
    assert client.timeout.read == catalog_client.TIMEOUT_SECONDS
