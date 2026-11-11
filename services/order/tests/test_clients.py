"""How order reads answers from cart (no database needed)."""

import asyncio
import time
import uuid

import httpx2
import pytest

from app import clients, events


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(clients, "BACKOFF_SECONDS", 0)


def answering(status: int, body: dict | None = None) -> httpx2.AsyncClient:
    def handle(request):
        return httpx2.Response(status, json=body or {})

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handle))


def get_cart(client):
    return asyncio.run(clients.get_cart(client, "http://cart", uuid.uuid4()))


def test_cart_error_answer_means_unavailable():
    with pytest.raises(clients.ServiceUnavailable):
        get_cart(answering(503))


def test_gives_up_after_max_attempts_without_an_answer():
    attempts = []

    def handle(request):
        attempts.append(request)
        raise httpx2.ConnectTimeout("timed out", request=request)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))

    with pytest.raises(clients.ServiceUnavailable):
        get_cart(client)
    assert len(attempts) == clients.MAX_ATTEMPTS


def test_gives_up_when_a_service_hangs_longer_than_the_budget(monkeypatch):
    monkeypatch.setattr(clients, "BUDGET_SECONDS", 0.2)

    async def answer_too_late(request):
        await asyncio.sleep(5)
        return httpx2.Response(200, json={})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(answer_too_late))

    start = time.perf_counter()
    with pytest.raises(clients.ServiceUnavailable):
        get_cart(client)
    assert time.perf_counter() - start < 1


def test_cart_call_and_publishing_together_fit_inside_the_gateways_budget_for_order():
    # The gateway waits 5s for order (services/gateway/app/routes.py, BUDGETS).
    # Placing an order calls cart, writes to the database, then publishes an event.
    assert clients.BUDGET_SECONDS + events.BUDGET_SECONDS < 5.0 - 1.0  # 1s for the DB
