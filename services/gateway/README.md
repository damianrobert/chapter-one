# gateway

The single front door of Chapter One. The frontend talks only to the gateway; it
routes each request to the right service. Later it also checks the user's login
(JWT) and adds a correlation ID. It has no database.

## Endpoints

| Method | Path            | Description                                          |
|--------|-----------------|------------------------------------------------------|
| GET    | `/health/live`  | Process is up                                        |
| GET    | `/health/ready` | Always ready once running (doesn't check other services — see `app/main.py`) |

Routing (`/api/books...` → catalog, `/api/carts...` → cart) comes in the next pieces.

Errors look like `{"error": {"code": "...", "message": "..."}}`, e.g. 404 `NOT_FOUND`.

## Configuration (env vars)

| Variable    | Default | Description |
|-------------|---------|-------------|
| `PORT`      | `8000`  | HTTP port   |
| `LOG_LEVEL` | `INFO`  | Log level   |

## Logs

One JSON object per line on stdout, same format as catalog (`app/logging_config.py`
is a copy of catalog's).

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest
ruff check . && ruff format --check .
```

## Run with Docker

From the repo root: `docker compose up --build gateway`. Then
`curl localhost:8000/health/live`. Image `chapter-one/gateway`, runs as non-root (UID 10001).
