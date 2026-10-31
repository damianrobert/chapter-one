# catalog

Owns books, authors and prices. Other services call it; it calls nobody.

## Endpoints

| Method | Path           | Description                      |
|--------|----------------|----------------------------------|
| GET    | `/health/live` | Process is up (no dependency checks) |

## Configuration (env vars)

| Variable    | Default | Description          |
|-------------|---------|----------------------|
| `PORT`      | `8001`  | HTTP port            |
| `LOG_LEVEL` | `INFO`  | Log level            |

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m app          # http://localhost:8001/health/live
pytest
ruff check . && ruff format --check .
```

## Run with Docker

From the repo root: `docker compose up --build catalog`. The image is `chapter-one/catalog`
and runs as non-root (UID 10001).
