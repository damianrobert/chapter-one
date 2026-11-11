"""Forwarding ("proxying") a request from the gateway to a service behind it.

The frontend calls   GET /api/books?q=austen   on the gateway.
The gateway calls    GET /books?q=austen       on catalog, and hands back the answer.

Two rules from CLAUDE.md shape this file (hard rule 6):

1. **Every call has a time budget.** Without one, a hung catalog would make the
   gateway wait forever. Requests would pile up until the gateway itself fell over,
   so one slow service would bring down everything.
   The budget covers the *whole* call, retries and pauses included, so "2s" really
   means the client has an answer within about 2s. Each service gets its own budget
   (see BUDGETS in app/routes.py and docs/decisions/0002-timeout-budgets.md).

2. **Retry only requests that are safe to repeat.** If a call fails because of a
   network problem, we don't know whether catalog received it. Repeating a GET is
   harmless, since it only reads. Repeating a POST could create the same book twice,
   so POST, PUT, PATCH and DELETE are tried exactly once.
   Retries wait a little longer each time ("backoff": 0.2s, then 0.4s). That gives a
   restarting service a moment to come back, instead of hammering it.

If catalog can't be reached at all, the client gets a clear 503 in our error shape.
If catalog *does* answer, even with an error like 404, that answer is passed on
unchanged. It's catalog's answer, and the gateway has no reason to rewrite it.
"""

import asyncio
import logging

import httpx2
from fastapi import Request, Response

from app.errors import error_response

logger = logging.getLogger(__name__)

DEFAULT_BUDGET_SECONDS = 2.0  # hard rule 6's default
MAX_ATTEMPTS = 3  # first try + 2 retries (only for safe methods)
BACKOFF_SECONDS = 0.2  # pause before retry 1; doubles for each further retry
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}  # read-only, so safe to repeat

# Only these headers are passed along. Copying *all* headers would also copy things
# that describe the connection between the browser and the gateway (Host,
# Content-Length, Connection...), which are wrong for the gateway's own call.
REQUEST_HEADERS_TO_FORWARD = ("content-type", "accept")
RESPONSE_HEADERS_TO_FORWARD = ("content-type", "location")


def create_client() -> httpx2.AsyncClient:
    """One shared HTTP client for the whole gateway.

    It keeps connections to the services open and reuses them, which is much faster
    than opening a new connection for every request. This timeout is only a fallback:
    `forward` sets each service's own budget on every call.
    """
    return httpx2.AsyncClient(timeout=DEFAULT_BUDGET_SECONDS)


async def _send_with_retries(
    client: httpx2.AsyncClient,
    method: str,
    url: str,
    service: str,
    budget_seconds: float,
    **kwargs,
) -> httpx2.Response:
    """Send the request, retrying safe methods, all within `budget_seconds`.

    asyncio.timeout() is a stopwatch around the whole loop: when the budget is used
    up it interrupts whatever is running (an attempt or a pause) with TimeoutError.
    In practice that means quick failures (connection refused) get retried, but a
    service that hangs uses up the budget once and isn't waited for again.
    """
    attempts = MAX_ATTEMPTS if method in SAFE_METHODS else 1
    async with asyncio.timeout(budget_seconds):
        for attempt in range(1, attempts + 1):
            try:
                # timeout= limits one attempt; the stopwatch above limits all of them.
                return await client.request(method, url, timeout=budget_seconds, **kwargs)
            except httpx2.TransportError as exc:
                # TransportError = no answer at all: connection refused, timed out, etc.
                if attempt == attempts:
                    raise
                delay = BACKOFF_SECONDS * 2 ** (attempt - 1)
                logger.warning(
                    "call to %s failed, retrying",
                    service,
                    extra={"upstream": service, "attempt": attempt, "error": repr(exc)},
                )
                await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # the loop always returns or raises


async def forward(
    request: Request,
    client: httpx2.AsyncClient,
    service: str,
    base_url: str,
    path: str,
    user_id: str | None = None,
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
) -> Response:
    """Send `request` to `base_url + path` and return the service's answer.

    `user_id` is the logged-in user, if any, and is passed on as `X-User-ID`.
    """
    url = base_url.rstrip("/") + path
    if request.url.query:
        url += "?" + request.url.query  # pass ?q=...&limit=... through unchanged

    headers = {
        name: request.headers[name]
        for name in REQUEST_HEADERS_TO_FORWARD
        if name in request.headers
    }
    # Services trust this header (hard rule 8), so only the gateway may set it. A value
    # sent by the client is never copied, because it isn't in the list above.
    if user_id is not None:
        headers["X-User-ID"] = user_id

    try:
        upstream = await _send_with_retries(
            client,
            request.method,
            url,
            service,
            budget_seconds,
            headers=headers,
            content=await request.body(),
        )
    except (httpx2.TransportError, TimeoutError) as exc:
        # No answer at all, or no answer within the budget: same thing for the client.
        logger.error(
            "%s unavailable, giving up",
            service,
            extra={"upstream": service, "error": repr(exc)},
        )
        return error_response(
            503, "SERVICE_UNAVAILABLE", f"The {service} service is unavailable, try again later"
        )

    response_headers = {
        name: upstream.headers[name]
        for name in RESPONSE_HEADERS_TO_FORWARD
        if name in upstream.headers
    }
    # Services don't know about the gateway's /api prefix. A new book's Location is
    # "/books/<id>" from catalog's point of view; for the client it's "/api/books/<id>".
    location = response_headers.get("location")
    if location and location.startswith("/"):
        response_headers["location"] = "/api" + location

    return Response(
        content=upstream.content, status_code=upstream.status_code, headers=response_headers
    )
