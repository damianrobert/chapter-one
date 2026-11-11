# Same multi-stage, non-root pattern as services/catalog/Dockerfile (see the comments
# there). Redis needs no migrations, so there's no entrypoint script.

# ---------- Stage 1: build dependencies ----------
FROM python:3.12-slim AS builder

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY app ./app
# The shared event definitions (repo-root shared/), handed in by docker-compose.yml as
# the extra build context "shared" (same as order and inventory). Code files only.
COPY --from=shared __init__.py events.py messaging.py ./shared/

USER 10001

EXPOSE 8002

# Exec form: python is PID 1 and gets SIGTERM directly, so uvicorn shuts down gracefully.
CMD ["python", "-m", "app"]
