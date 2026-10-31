# Multi-stage build:
#   1. "builder" installs dependencies into a virtualenv (needs pip, maybe compilers).
#   2. "runtime" copies only that virtualenv + our code into a clean slim image.
# Build tools and pip caches never reach the final image -> smaller, less attack surface.

# ---------- Stage 1: build dependencies ----------
FROM python:3.12-slim AS builder

# A virtualenv at a fixed path is easy to copy as one directory into the next stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements first: Docker caches this layer, so editing app code
# doesn't trigger a full reinstall of dependencies on every build.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

# PYTHONDONTWRITEBYTECODE: no .pyc files (container filesystem is throwaway anyway).
# PYTHONUNBUFFERED: print logs immediately to stdout, so `docker logs` sees them live.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Non-root user with a fixed numeric UID. Kubernetes' `runAsNonRoot` check
# can only verify numeric UIDs, so we don't rely on a username.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY app ./app

USER 10001

EXPOSE 8001

# Exec form (JSON array) makes python PID 1, so it receives SIGTERM directly from
# `docker stop` / Kubernetes and uvicorn can shut down gracefully. The shell form
# (`CMD python -m app`) would wrap it in /bin/sh, which doesn't forward signals.
CMD ["python", "-m", "app"]
