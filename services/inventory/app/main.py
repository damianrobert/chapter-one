import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import db, stock
from app.config import settings
from app.consumer import OrderCreatedConsumer
from app.errors import register_error_handlers

logger = logging.getLogger(__name__)
request_logger = logging.getLogger("app.requests")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: runs once before the first request is accepted.
    app.state.engine = db.create_engine(settings.database_url)
    # Takes OrderCreated events from RabbitMQ; connects in the background (app/consumer.py).
    app.state.consumer = OrderCreatedConsumer(settings.rabbitmq_url, app.state.engine)
    app.state.consumer.start()
    yield
    # Shutdown: runs after uvicorn has stopped accepting requests and in-flight
    # requests finished (e.g. on SIGTERM). Stop taking events first (they use the
    # database), then close pooled DB connections cleanly.
    await app.state.consumer.stop()
    await app.state.engine.dispose()


app = FastAPI(title="Chapter One — Inventory", lifespan=lifespan)
register_error_handlers(app)
app.include_router(stock.router)


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
    # Readiness: "can I do my job right now?" Inventory's job is handling events, so
    # besides the database it checks that the event consumer is connected.
    checks = {"database": "ok", "broker": "ok"}
    try:
        await db.ping_database(request.app.state.engine)
    except Exception:
        logger.warning("readiness check failed: database unreachable", exc_info=True)
        checks["database"] = "unreachable"
    if not request.app.state.consumer.is_consuming():
        checks["broker"] = "unreachable"
    if all(result == "ok" for result in checks.values()):
        return JSONResponse(content={"status": "ok", "checks": checks})
    return JSONResponse(status_code=503, content={"status": "unavailable", "checks": checks})
