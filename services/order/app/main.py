import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import clients, db, events, orders
from app.config import settings
from app.errors import register_error_handlers

logger = logging.getLogger(__name__)
request_logger = logging.getLogger("app.requests")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: runs once before the first request is accepted.
    app.state.engine = db.create_engine(settings.database_url)
    # One HTTP client for calls to cart; it keeps connections open.
    app.state.http_client = clients.create_client()
    # Publishes events to RabbitMQ. Doesn't connect yet (see app/events.py).
    app.state.publisher = events.EventPublisher(settings.rabbitmq_url, settings.service_name)
    yield
    # Shutdown: runs after uvicorn has stopped accepting requests and in-flight
    # requests finished (e.g. on SIGTERM). Close connections cleanly.
    await app.state.publisher.close()
    await app.state.http_client.aclose()
    await app.state.engine.dispose()


app = FastAPI(title="Chapter One — Order", lifespan=lifespan)
register_error_handlers(app)
app.include_router(orders.router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log one JSON line per request: method, path, status and duration."""
    start = time.perf_counter()
    status = 500  # used if the endpoint crashes with an error nobody handled
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        # Health checks run every few seconds; log them at DEBUG so they don't drown
        # out real traffic at the INFO level.
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
    # Liveness: "is the process running?" Checks no dependencies — restarting the
    # container wouldn't fix a down database.
    return {"status": "ok", "service": settings.service_name}


@app.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    # Readiness: "can I serve real traffic right now?" Checks the database and RabbitMQ:
    # without the broker, placing an order fails. (Not cart: like the gateway, order
    # stays "ready" when cart is down and answers 503 only where cart is needed.)
    checks = {
        "database": await _check("database", db.ping_database(request.app.state.engine)),
        "broker": await _check("broker", request.app.state.publisher.check()),
    }
    if all(result == "ok" for result in checks.values()):
        return JSONResponse(content={"status": "ok", "checks": checks})
    return JSONResponse(status_code=503, content={"status": "unavailable", "checks": checks})


async def _check(name: str, probe) -> str:
    try:
        await probe
    except Exception:
        logger.warning("readiness check failed: %s unreachable", name, exc_info=True)
        return "unreachable"
    return "ok"
