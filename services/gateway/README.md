# gateway

The single front door of Chapter One. The frontend talks only to the gateway; it
routes each request to the right service. Later it also checks the user's login
(JWT) and adds a correlation ID. It has no database.

## Endpoints

| Method | Path            | Description                                          |
|--------|-----------------|------------------------------------------------------|
| GET    | `/health/live`  | Process is up                                        |
| GET    | `/health/ready` | Always ready once running (doesn't check other services — see `app/main.py`) |
| any    | `/api/books`, `/api/books/...` | Forwarded to catalog as `/books...` (see catalog's README) |

`/api/carts...` → cart comes in a later piece. The routing table is `app/routes.py`.

How forwarding works (`app/proxy.py`):
- Every call to a service has a **2s timeout**.
- `GET`/`HEAD`/`OPTIONS` are retried up to 3 attempts on network errors (pauses 0.2s, 0.4s).
  `POST`/`PUT`/`PATCH`/`DELETE` are tried once (repeating could e.g. create a book twice).
- If the service can't be reached: 503 `SERVICE_UNAVAILABLE`.
- If the service answers (even 404 or 400), its answer is passed on unchanged.
- A `Location` header gets the `/api` prefix added (`/books/<id>` → `/api/books/<id>`).

Gateway errors look like `{"error": {"code": "...", "message": "..."}}`, e.g. 404 `NOT_FOUND`
for unknown paths.

## Configuration (env vars)

| Variable      | Default    | Description                               |
|---------------|------------|-------------------------------------------|
| `PORT`        | `8000`     | HTTP port                                 |
| `LOG_LEVEL`   | `INFO`     | Log level                                 |
| `CATALOG_URL` | (required) | Catalog's address, e.g. `http://catalog:8001` |

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

From the repo root: `docker compose up --build` (the gateway needs catalog). Then
`curl localhost:8000/api/books`. Image `chapter-one/gateway`, runs as non-root (UID 10001).
