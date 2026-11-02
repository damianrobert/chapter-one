"""The API gateway: the single front door of Chapter One.

The frontend only ever talks to the gateway. The gateway decides which service
handles a request (routing), and later checks who the user is (JWT) and adds a
correlation ID. Services behind it stay simple and never face the internet directly.

The routing table is in app/routes.py; how a request is forwarded is in app/proxy.py.
"""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app import proxy, routes
from app.config import settings
from app.errors import register_error_handlers

request_logger = logging.getLogger("app.requests")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: one HTTP client, shared by all requests (it reuses connections).
    app.state.http_client = proxy.create_client()
    yield
    # Shutdown (e.g. SIGTERM): runs after in-flight requests finished; close connections.
    await app.state.http_client.aclose()


app = FastAPI(title="Chapter One — API Gateway", lifespan=lifespan)
register_error_handlers(app)
app.include_router(routes.router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log one JSON line per request: method, path, status and duration.

    Same middleware as in catalog. At the gateway these lines are especially useful:
    every request from the frontend passes through here, so this is a complete
    record of all traffic into the system.
    """
    start = time.perf_counter()
    status = 500  # used if the endpoint crashes with an error nobody handled
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        # Health checks run every few seconds; log them at DEBUG to keep INFO readable.
        level = logging.DEBUG if request.url.path.startswith("/health/") else logging.INFO
        request_logger.log(
            level,
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "durationMs": round((time.perf_counter() - start) * 1000, 1),
            },
        )


@app.get("/health/live")
async def live() -> dict:
    # Liveness: "is the process running and able to answer?" No dependency checks.
    return {"status": "ok", "service": settings.service_name}


@app.get("/health/ready")
async def ready() -> dict:
    # Readiness: "can I serve traffic right now?" The gateway has no database or
    # broker of its own, so the answer is yes as soon as it's running.
    #
    # Why NOT check catalog/cart here: if catalog went down, the gateway would report
    # "not ready" and Kubernetes would stop sending it ANY traffic — so cart and order
    # would become unreachable too, just because catalog is down. One broken service
    # would take everything down with it. Instead, the gateway stays up and answers
    # 503 only for requests that need the broken service (see app/proxy.py).
    return {"status": "ok", "checks": {}}
