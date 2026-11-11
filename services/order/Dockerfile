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
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
# The shared event definitions (repo-root shared/). This image builds from
# services/order/, which can't see ../../shared, so docker-compose.yml hands that folder
# in as an extra build context named "shared". Only the code files, not its tests.
COPY --from=shared __init__.py events.py messaging.py ./shared/
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

USER 10001

EXPOSE 8004

# The entrypoint runs migrations, then `exec`s the CMD. Exec form (JSON array) for
# both means no extra shell stays around: python ends up as PID 1, receives SIGTERM
# directly from `docker stop` / Kubernetes, and uvicorn can shut down gracefully.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "app"]
