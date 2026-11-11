"""How order reads answers from cart and inventory (no database needed)."""

import asyncio
import time
import uuid

import httpx2
import pytest

from app import clients

ORDER_ID = uuid.uuid4()
ITEMS = [{"bookId": str(uuid.uuid4()), "quantity": 1}]


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(clients, "BACKOFF_SECONDS", 0)


def answering(status: int, body: dict | None = None) -> httpx2.AsyncClient:
    def handle(request):
        return httpx2.Response(status, json=body or {})

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handle))


def reserve(client):
    return asyncio.run(clients.reserve_stock(client, "http://inventory", ORDER_ID, ITEMS))


@pytest.mark.parametrize("status", [200, 201])
def test_reserved_now_or_already_reserved_both_count_as_success(status):
    reserve(answering(status))  # no exception


def test_insufficient_stock_is_reported_with_details():
    details = [{"bookId": ITEMS[0]["bookId"], "requested": 1}]
    error = {"error": {"code": "INSUFFICIENT_STOCK", "message": "...", "details": details}}

    with pytest.raises(clients.InsufficientStock) as caught:
        reserve(answering(409, error))

    assert caught.value.details == details


@pytest.mark.parametrize(
    "status, body",
    [
        (503, {"error": {"code": "DATABASE_UNAVAILABLE"}}),
        (409, {"error": {"code": "RESERVATION_CONFLICT"}}),  # a 409 that isn't "no stock"
    ],
)
def test_other_inventory_answers_mean_unavailable(status, body):
    with pytest.raises(clients.ServiceUnavailable):
        reserve(answering(status, body))


def test_cart_error_answer_means_unavailable():
    with pytest.raises(clients.ServiceUnavailable):
        asyncio.run(clients.get_cart(answering(503), "http://cart", uuid.uuid4()))


def test_gives_up_after_max_attempts_without_an_answer():
    attempts = []

    def handle(request):
        attempts.append(request)
        raise httpx2.ConnectTimeout("timed out", request=request)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))

    with pytest.raises(clients.ServiceUnavailable):
        reserve(client)
    assert len(attempts) == clients.MAX_ATTEMPTS


def test_gives_up_when_a_service_hangs_longer_than_the_budget(monkeypatch):
    monkeypatch.setattr(clients, "BUDGET_SECONDS", 0.2)

    async def answer_too_late(request):
        await asyncio.sleep(5)
        return httpx2.Response(201, json={})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(answer_too_late))

    start = time.perf_counter()
    with pytest.raises(clients.ServiceUnavailable):
        reserve(client)
    assert time.perf_counter() - start < 1


def test_both_calls_together_fit_inside_the_gateways_budget_for_order():
    # The gateway waits 5s for order (services/gateway/app/routes.py, BUDGETS).
    # Placing an order calls cart, then inventory, then writes to the database.
    assert 2 * clients.BUDGET_SECONDS < 5.0 - 1.0  # leave 1s for the database
