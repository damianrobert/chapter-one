# cart

Holds what each user has put in their cart, in Redis. Carts are temporary: they'll
expire after ~7 days without changes. Later, cart asks catalog whether a book exists
and what it costs.

**Status:** skeleton only (health checks, logs, Redis connection). Cart endpoints
come in the next piece.

## Endpoints

| Method | Path            | Description                                    |
|--------|-----------------|------------------------------------------------|
| GET    | `/health/live`  | Process is up                                  |
| GET    | `/health/ready` | Redis answers `PING` (503 if not)              |

Errors look like `{"error": {"code": "...", "message": "..."}}`.

## Configuration (env vars)

| Variable    | Default    | Description                                   |
|-------------|------------|-----------------------------------------------|
| `PORT`      | `8002`     | HTTP port                                     |
| `LOG_LEVEL` | `INFO`     | Log level                                     |
| `REDIS_URL` | (required) | e.g. `redis://cart-redis:6379/0`              |

Redis calls time out after 2s (connect and each command).

## Inspect the data

Redis has no host port published (only cart may use it). To look inside:

```bash
docker compose exec cart-redis redis-cli
> KEYS *
```

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest
ruff check . && ruff format --check .
```

## Run with Docker

From the repo root: `docker compose up --build`. Then `curl localhost:8002/health/ready`.
Image `chapter-one/cart`, runs as non-root (UID 10001).
