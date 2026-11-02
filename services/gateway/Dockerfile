# Same multi-stage, non-root pattern as services/catalog/Dockerfile (see the comments
# there). The gateway has no database, so no migrations and no entrypoint script.

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

USER 10001

EXPOSE 8000

# Exec form: python is PID 1 and gets SIGTERM directly, so uvicorn shuts down gracefully.
CMD ["python", "-m", "app"]
