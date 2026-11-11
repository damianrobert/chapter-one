"""Calls from order to the cart service.

Order never reads cart's Redis (hard rule 1). It asks over cart's public HTTP API, like
any other client would: `GET /carts/{userId}`, "what is in this user's cart?"

(In step 3 this file also asked inventory to reserve stock over HTTP. From step 4 on,
order publishes an `OrderCreated` event instead; see app/events.py.)

Hard rule 6 applies:

- **A 1.5s budget for the call**, retries and pauses included, so a hung cart can't
  make order hang too. See docs/decisions/0002-timeout-budgets.md.
- **Retry with backoff** (3 attempts, pauses 0.2s then 0.4s), but only when there was
  no answer at all (connection refused, timeout) and budget is left. Safe here because
  the cart GET only reads.
"""

import asyncio
import logging
from uuid import UUID

import httpx2

logger = logging.getLogger(__name__)

BUDGET_SECONDS = 1.5  # per call, all attempts included
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 0.2  # pause before retry 1; doubles for each further retry


class ServiceUnavailable(Exception):
    """A service didn't answer, or answered with something we can't use."""

    def __init__(self, service: str, message: str):
        super().__init__(message)
        self.service = service


def create_client() -> httpx2.AsyncClient:
    """One shared HTTP client for the whole service (it reuses connections)."""
    return httpx2.AsyncClient(timeout=BUDGET_SECONDS)


async def _send(
    client: httpx2.AsyncClient, service: str, method: str, url: str, **kwargs
) -> httpx2.Response:
    """Send a request within BUDGET_SECONDS, retrying only when no answer came back.

    Callers must only use this for requests that are safe to repeat (see above).
    """
    try:
        # A stopwatch around all attempts: when the budget runs out it interrupts
        # whatever is running (an attempt or a pause) with TimeoutError.
        async with asyncio.timeout(BUDGET_SECONDS):
            return await _send_with_retries(client, service, method, url, **kwargs)
    except (httpx2.TransportError, TimeoutError) as exc:
        logger.error(
            "service unavailable, giving up",
            extra={"upstream": service, "error": repr(exc)},
        )
        raise ServiceUnavailable(service, f"{service} did not answer") from exc


async def _send_with_retries(
    client: httpx2.AsyncClient, service: str, method: str, url: str, **kwargs
) -> httpx2.Response:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await client.request(method, url, **kwargs)
        except httpx2.TransportError as exc:
            if attempt == MAX_ATTEMPTS:
                raise
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
