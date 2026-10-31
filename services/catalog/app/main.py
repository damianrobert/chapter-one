import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import books, db
from app.config import settings
from app.errors import register_error_handlers

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: runs once before the first request is accepted.
    app.state.engine = db.create_engine(settings.database_url)
    yield
    # Shutdown: runs after uvicorn has stopped accepting requests and in-flight
    # requests finished (e.g. on SIGTERM). Close pooled DB connections cleanly.
    await app.state.engine.dispose()


app = FastAPI(title="Chapter One — Catalog", lifespan=lifespan)
register_error_handlers(app)
app.include_router(books.router)


@app.get("/health/live")
async def live() -> dict:
    # Liveness: "is the process running and able to answer?"
    # Deliberately checks NO dependencies. In Kubernetes a failing liveness probe
    # restarts the container — restarting won't fix a down database, so that belongs
    # in /health/ready.
    return {"status": "ok", "service": settings.service_name}


@app.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    # Readiness: "can I serve real traffic right now?" Checks dependencies.
    # In Kubernetes a failing readiness probe only takes the pod out of the load
    # balancer (no restart); it gets traffic again once the DB is back.
    try:
        await db.ping_database(request.app.state.engine)
    except Exception:
        logger.warning("readiness check failed: database unreachable", exc_info=True)
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "checks": {"database": "unreachable"}},
        )
    return JSONResponse(content={"status": "ok", "checks": {"database": "ok"}})
