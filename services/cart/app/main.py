"""The cart service: what each user has put in their cart, stored in Redis.

This file wires things together: Redis and HTTP clients, the event consumer, health
checks, request logging. The cart endpoints are in app/carts.py; calls to catalog in
app/catalog_client.py; emptying the cart after an order in app/consumer.py.
"""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import carts, catalog_client, redis_client
from app.config import settings
from app.consumer import OrderConfirmedConsumer
from app.errors import register_error_handlers

logger = logging.getLogger(__name__)
request_logger = logging.getLogger("app.requests")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: one Redis client and one HTTP client for the whole service. Both keep
    # a pool of open connections that all requests share.
    app.state.redis = redis_client.create_client(settings.redis_url)
    app.state.http_client = catalog_client.create_client()
    # Empties carts when orders are confirmed; connects in the background (app/consumer.py).
    app.state.consumer = OrderConfirmedConsumer(settings.rabbitmq_url, app.state.redis)
    app.state.consumer.start()
    yield
    # Shutdown (e.g. SIGTERM): runs after in-flight requests finished. Stop taking
    # events first (they use Redis), then close connections.
    await app.state.consumer.stop()
    await app.state.http_client.aclose()
    await app.state.redis.aclose()


app = FastAPI(title="Chapter One — Cart", lifespan=lifespan)
register_error_handlers(app)
app.include_router(carts.router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log one JSON line per request: method, path, status and duration.

    Same middleware as in catalog and gateway.
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
    # Liveness: "is the process running?" No dependency checks — restarting the cart
    # wouldn't fix a broken Redis.
    return {"status": "ok", "service": settings.service_name}


@app.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    # Readiness: "can I serve traffic right now?" The cart is useless without Redis,
    # so Redis must answer. (Catalog is NOT checked here even though the cart will
    # call it — same reasoning as the gateway: one broken service shouldn't mark
    # others as not ready.) RabbitMQ is checked: without it, confirmed orders don't
    # empty carts ("consumer").
    checks = {"redis": "ok", "consumer": "ok"}
    try:
        await redis_client.ping_redis(request.app.state.redis)
    except Exception:
        logger.warning("readiness check failed: redis unreachable", exc_info=True)
        checks["redis"] = "unreachable"
    if not request.app.state.consumer.is_consuming():
        checks["consumer"] = "unreachable"
    if all(result == "ok" for result in checks.values()):
        return JSONResponse(content={"status": "ok", "checks": checks})
    return JSONResponse(status_code=503, content={"status": "unavailable", "checks": checks})
