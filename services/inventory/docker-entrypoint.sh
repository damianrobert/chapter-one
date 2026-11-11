#!/bin/sh
# Container entrypoint: apply DB migrations, then start the app.
#
# Running migrations on startup is fine with ONE container (Docker Compose).
# With several replicas (Kubernetes) they'd all race to migrate at once, so there
# this moves to a Kubernetes Job or initContainer that runs before the app pods.
set -e  # stop if migrations fail — never start the app against an old schema

alembic upgrade head

# `exec` replaces this shell with the app process, so the app becomes PID 1 and
# receives SIGTERM directly (same reason the CMD uses exec form).
exec "$@"
