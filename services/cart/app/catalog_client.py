"""Asking the catalog service about a book.

Before a book goes into a cart, the cart asks catalog two things: "does this book
exist?" and "what does it cost?". The cart never reads catalog's database (hard rule
1: no shared databases) — it uses catalog's public API, `GET /books/{id}`, like
anyone else would.

Hard rule 6 applies, the same way as in the gateway (services/gateway/app/proxy.py):

- **2s timeout** on every call, so a hung catalog can't make the cart hang too.
- **Retry with backoff** (3 attempts, pauses 0.2s then 0.4s), but only when there was
  no answer at all (connection refused, timeout). A GET only reads, so repeating it
  is harmless.

If catalog can't be reached, this raises `CatalogUnavailable` and the cart answers
503: "can't add books right now, try again later". Reading or removing items still
works, because those don't need catalog.
"""

import asyncio
import logging
from uuid import UUID

import httpx2

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 2.0
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 0.2  # pause before retry 1; doubles for each further retry


class CatalogUnavailable(Exception):
    """Catalog didn't answer, or answered with a server error."""


def create_client() -> httpx2.AsyncClient:
    """One shared HTTP client for the whole service (it reuses connections)."""
    return httpx2.AsyncClient(timeout=TIMEOUT_SECONDS)


async def get_book(client: httpx2.AsyncClient, catalog_url: str, book_id: UUID) -> dict | None:
    """Return the book as catalog describes it, or None if catalog says 404."""
    url = f"{catalog_url.rstrip('/')}/books/{book_id}"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.get(url)
            break
        except httpx2.TransportError as exc:
            if attempt == MAX_ATTEMPTS:
                logger.error(
                    "catalog unavailable, giving up",
                    extra={"upstream": "catalog", "error": repr(exc)},
                )
                raise CatalogUnavailable("catalog did not answer") from exc
            logger.warning(
                "call to catalog failed, retrying",
                extra={"upstream": "catalog", "attempt": attempt, "error": repr(exc)},
            )
            await asyncio.sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))

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
