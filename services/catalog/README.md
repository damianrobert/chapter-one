# catalog

Owns books, authors and prices. Other services call it; it calls nobody.

## Endpoints

| Method | Path           | Description                      |
|--------|----------------|----------------------------------|
| GET    | `/health/live` | Process is up (no dependency checks) |
| GET    | `/health/ready`| Database reachable (200), otherwise 503 |

## Configuration (env vars)

| Variable       | Default    | Description                                              |
|----------------|------------|----------------------------------------------------------|
| `PORT`         | `8001`     | HTTP port                                                |
| `LOG_LEVEL`    | `INFO`     | Log level                                                |
| `DATABASE_URL` | (required) | `postgresql+asyncpg://user:pass@host:5432/catalog_db`    |

## Run locally

Tests don't need a database (the DB check is faked). To run the app itself you need
Postgres, so the simplest way is Docker (below).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest
ruff check . && ruff format --check .
```

## Run with Docker

From the repo root: `docker compose up --build catalog`. The image is `chapter-one/catalog`
and runs as non-root (UID 10001).
