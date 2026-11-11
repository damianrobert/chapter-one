# order

Owns orders and their status. At checkout it copies what it needs from the cart
(titles, prices) into its own tables, so an order never changes afterwards, whatever
happens in catalog.

In roadmap step 3 it asks inventory over HTTP to reserve stock and waits for the answer.
From step 4 on that becomes events via RabbitMQ (the checkout saga in `CLAUDE.md`).

## Endpoints

All `/orders` endpoints need the `X-User-ID` header (set by the gateway from the login
token; hard rule 8). Missing or not a UUID → 400 `VALIDATION_ERROR`. You only ever see
your own orders.

| Method | Path            | Description                                                |
|--------|-----------------|------------------------------------------------------------|
| POST   | `/orders`       | Place an order from your cart (no body). 201 + the order   |
| GET    | `/orders`       | Your orders, newest first. `limit` (1–100, default 20), `offset` |
| GET    | `/orders/{id}`  | One of your orders; someone else's or unknown → 404 `ORDER_NOT_FOUND` |
| GET    | `/health/live`  | Process is up (no dependency checks)                       |
| GET    | `/health/ready` | Database reachable (200), otherwise 503                    |

What `POST /orders` does (step 3, synchronous):

1. `GET /carts/{userId}` on cart. Cart down → 503 `CART_UNAVAILABLE`; empty → 409 `CART_EMPTY`.
2. Saves the order as `PENDING`, copying title, author and price from the cart.
3. `PUT /reservations/{orderId}` on inventory, then:
   - reserved → `CONFIRMED` (201)
   - not enough stock → `CANCELLED`, `cancelReason: "INSUFFICIENT_STOCK"` (201)
   - inventory down → order stays `PENDING`, 503 `INVENTORY_UNAVAILABLE` with the
     `orderId` in `details`

Calls to cart and inventory: 1.5s budget per call (retries included), up to 3 attempts
when there's no answer (`app/clients.py`; why: `docs/decisions/0002-timeout-budgets.md`).
The cart is not emptied yet (that comes with events in step 4/5).

```bash
curl -X POST localhost:8004/orders -H "X-User-ID: <uuid>"
```

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
| `CART_URL`     | (required) | Cart service, e.g. `http://cart:8002`                    |
| `INVENTORY_URL`| (required) | Inventory service, e.g. `http://inventory:8003`          |

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
