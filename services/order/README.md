# order

Owns orders and their status. At checkout it copies what it needs from the cart
(titles, prices) into its own tables, so an order never changes afterwards, whatever
happens in catalog.

In roadmap step 3 it asks inventory over HTTP to reserve stock and waits for the answer.
From step 4 on that becomes events via RabbitMQ (the checkout saga in `CLAUDE.md`).

## Endpoints

| Method | Path            | Description                               |
|--------|-----------------|-------------------------------------------|
| GET    | `/health/live`  | Process is up (no dependency checks)      |
| GET    | `/health/ready` | Database reachable (200), otherwise 503   |

The orders endpoints (`POST /orders`, `GET /orders`, `GET /orders/{id}`) come in the
next piece.

## Data

- `orders`: `id`, `user_id`, `status` (`PENDING` / `CONFIRMED` / `CANCELLED`, enforced
  by a DB check), `cancel_reason` (only on cancelled orders), `total_minor` + `currency`,
  timestamps. Indexed by `(user_id, created_at)` for "my orders".
- `order_items`: one row per book, with **copies** of `title`, `author` and
  `unit_price_minor` taken at checkout (hard rule 2), plus `quantity`.

```bash
docker compose exec order-db psql -U order -d order_db -c 'select * from orders'
```

## Configuration (env vars)

| Variable       | Default    | Description                                              |
|----------------|------------|----------------------------------------------------------|
| `PORT`         | `8004`     | HTTP port                                                |
| `LOG_LEVEL`    | `INFO`     | Log level (`DEBUG` also shows health-check requests)     |
| `DATABASE_URL` | (required) | `postgresql+asyncpg://user:pass@host:5432/order_db`      |

## Run locally

Tests marked `db` start a throwaway Postgres with testcontainers, so **Docker must be
running**. Skip them with `pytest -m "not db"`.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest
ruff check . && ruff format --check .
```

Migrations work like catalog's (Alembic, run by `docker-entrypoint.sh` on start).

## Run with Docker

From the repo root: `docker compose up --build order`. Image `chapter-one/order`, runs as
non-root (UID 10001).
