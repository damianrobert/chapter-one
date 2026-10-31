from fastapi import FastAPI

from app.config import settings

app = FastAPI(title="Chapter One — Catalog")


@app.get("/health/live")
async def live() -> dict:
    # Liveness: "is the process running and able to answer?"
    # Deliberately checks NO dependencies. In Kubernetes a failing liveness probe
    # restarts the container — restarting won't fix a down database, so that belongs
    # in /health/ready (added together with PostgreSQL).
    return {"status": "ok", "service": settings.service_name}
