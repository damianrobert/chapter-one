"""Asking the catalog service about a book.

Before a book goes into a cart, the cart asks catalog two things: "does this book
exist?" and "what does it cost?". The cart never reads catalog's database (hard rule
1: no shared databases) — it uses catalog's public API, `GET /books/{id}`, like
anyone else would.

Hard rule 6 applies, the same way as in the gateway (services/gateway/app/proxy.py):

- **A 1.5s budget for the whole call**, retries and pauses included, so a hung
  catalog can't make the cart hang too. It's below the gateway's 2s for cart, so
  the cart can still answer (503) before the gateway gives up on it. See
  docs/decisions/0002-timeout-budgets.md.
- **Retry with backoff** (3 attempts, pauses 0.2s then 0.4s), but only when there was
  no answer at all (connection refused, timeout) and budget is left. A GET only
  reads, so repeating it is harmless.

If catalog can't be reached, this raises `CatalogUnavailable` and the cart answers
503: "can't add books right now, try again later". Reading or removing items still
works, because those don't need catalog.
"""

import asyncio
import logging
from uuid import UUID

import httpx2

logger = logging.getLogger(__name__)

BUDGET_SECONDS = 1.5  # whole call, all attempts included
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 0.2  # pause before retry 1; doubles for each further retry


class CatalogUnavailable(Exception):
    """Catalog didn't answer, or answered with a server error."""


def create_client() -> httpx2.AsyncClient:
    """One shared HTTP client for the whole service (it reuses connections)."""
    return httpx2.AsyncClient(timeout=BUDGET_SECONDS)


async def get_book(client: httpx2.AsyncClient, catalog_url: str, book_id: UUID) -> dict | None:
    """Return the book as catalog describes it, or None if catalog says 404."""
    url = f"{catalog_url.rstrip('/')}/books/{book_id}"

    try:
        # A stopwatch around all attempts: when the budget runs out it interrupts
        # whatever is running (an attempt or a pause) with TimeoutError.
        async with asyncio.timeout(BUDGET_SECONDS):
            response = await _get_with_retries(client, url)
    except (httpx2.TransportError, TimeoutError) as exc:
        logger.error(
            "catalog unavailable, giving up",
            extra={"upstream": "catalog", "error": repr(exc)},
        )
        raise CatalogUnavailable("catalog did not answer") from exc

    if response.status_code == 404:
        return None
    if response.status_code != 200:
        # Catalog answered, but with something unexpected (e.g. 503 because its own
        # database is down). For the cart that means the same thing: catalog can't
        # help right now. Not retried: catalog is up and gave a clear answer.
        logger.error(
            "catalog answered with an error",
            extra={"upstream": "catalog", "status": response.status_code},
        )
        raise CatalogUnavailable(f"catalog answered {response.status_code}")
    return response.json()


async def _get_with_retries(client: httpx2.AsyncClient, url: str) -> httpx2.Response:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await client.get(url)
        except httpx2.TransportError as exc:
            if attempt == MAX_ATTEMPTS:
                raise
            logger.warning(
                "call to catalog failed, retrying",
                extra={"upstream": "catalog", "attempt": attempt, "error": repr(exc)},
            )
            await asyncio.sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))
    raise AssertionError("unreachable")  # the loop always returns or raises
