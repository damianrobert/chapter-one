"""Calls from order to the cart and inventory services.

Order never reads cart's Redis or inventory's database (hard rule 1). It asks them
over their public HTTP APIs, like any other client would:

- **cart:**      `GET /carts/{userId}`             — "what is in this user's cart?"
- **inventory:** `PUT /reservations/{orderId}`     — "please set these books aside"

Hard rule 6 applies to both, the same way as in the gateway and the cart:

- **2s timeout** on every attempt, so a hung service can't make order hang too.
- **Retry with backoff** (3 attempts, pauses 0.2s then 0.4s), but only when there was
  no answer at all (connection refused, timeout).

Retrying is only safe because both calls are safe to repeat:

- The cart GET only reads.
- The inventory PUT is idempotent *by order id*: inventory remembers which order
  already reserved what (see services/inventory/app/reservations.py). If our first
  attempt did reserve the books but the answer got lost on the way back, the retry
  gets "already reserved" (200) instead of taking the copies a second time.

This is the step-3 design and it's fragile on purpose: while order waits for
inventory, the customer waits for order. Step 4 replaces the inventory call with
events via RabbitMQ.
"""

import asyncio
import logging
from uuid import UUID

import httpx2

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 2.0
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 0.2  # pause before retry 1; doubles for each further retry


class ServiceUnavailable(Exception):
    """A service didn't answer, or answered with something we can't use."""

    def __init__(self, service: str, message: str):
        super().__init__(message)
        self.service = service


class InsufficientStock(Exception):
    """Inventory answered: not enough copies of at least one book."""

    def __init__(self, details: list | None):
        super().__init__("insufficient stock")
        self.details = details


def create_client() -> httpx2.AsyncClient:
    """One shared HTTP client for the whole service (it reuses connections)."""
    return httpx2.AsyncClient(timeout=TIMEOUT_SECONDS)


async def _send(
    client: httpx2.AsyncClient, service: str, method: str, url: str, **kwargs
) -> httpx2.Response:
    """Send a request, retrying only when no answer came back at all.

    Callers must only use this for requests that are safe to repeat (see above).
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await client.request(method, url, **kwargs)
        except httpx2.TransportError as exc:
            if attempt == MAX_ATTEMPTS:
                logger.error(
                    "service unavailable, giving up",
                    extra={"upstream": service, "error": repr(exc)},
                )
                raise ServiceUnavailable(service, f"{service} did not answer") from exc
            logger.warning(
                "call failed, retrying",
                extra={"upstream": service, "attempt": attempt, "error": repr(exc)},
            )
            await asyncio.sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))
    raise AssertionError("unreachable")  # the loop always returns or raises


async def get_cart(client: httpx2.AsyncClient, cart_url: str, user_id: UUID) -> dict:
    """Return the user's cart exactly as the cart service describes it."""
    url = f"{cart_url.rstrip('/')}/carts/{user_id}"
    response = await _send(client, "cart", "GET", url)
    if response.status_code != 200:
        # E.g. 503 because cart's Redis is down. For order: cart can't help right now.
        logger.error(
            "cart answered with an error",
            extra={"upstream": "cart", "status": response.status_code},
        )
        raise ServiceUnavailable("cart", f"cart answered {response.status_code}")
    return response.json()


async def reserve_stock(
    client: httpx2.AsyncClient, inventory_url: str, order_id: UUID, items: list[dict]
) -> None:
    """Ask inventory to reserve `items` ([{bookId, quantity}]) for this order.

    Returns normally when reserved. Raises InsufficientStock when inventory says no,
    ServiceUnavailable when we don't know the outcome.
    """
    url = f"{inventory_url.rstrip('/')}/reservations/{order_id}"
    response = await _send(client, "inventory", "PUT", url, json={"items": items})

    # 201 = reserved now, 200 = this order had already reserved exactly this (a retry).
    if response.status_code in (200, 201):
        return
    if response.status_code == 409:
        error = response.json().get("error", {})
        if error.get("code") == "INSUFFICIENT_STOCK":
            raise InsufficientStock(error.get("details"))
    # Anything else (503, a 409 RESERVATION_CONFLICT that shouldn't happen, ...) means
    # we can't tell whether the books are reserved.
    logger.error(
        "inventory answered with an error",
        extra={"upstream": "inventory", "status": response.status_code},
    )
    raise ServiceUnavailable("inventory", f"inventory answered {response.status_code}")
