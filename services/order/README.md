# order

Owns orders and their status. At checkout it copies what it needs from the cart
(titles, prices) into its own tables, so an order never changes afterwards, whatever
happens in catalog.

It doesn't call inventory: it publishes an `OrderCreated` event to RabbitMQ and answers
right away; inventory's reply arrives later as an event, and order then confirms or
cancels the order (the checkout saga in `CLAUDE.md`, roadmap step 4).

## Endpoints

All `/orders` endpoints need the `X-User-ID` header (set by the gateway from the login
token; hard rule 8). Missing or not a UUID → 400 `VALIDATION_ERROR`. You only ever see
your own orders.

| Method | Path            | Description                                                |
|--------|-----------------|------------------------------------------------------------|
| POST   | `/orders`       | Place an order from your cart (no body). 202 + the PENDING order, `Location` header |
| GET    | `/orders`       | Your orders, newest first. `limit` (1–100, default 20), `offset` |
| GET    | `/orders/{id}`  | One of your orders; someone else's or unknown → 404 `ORDER_NOT_FOUND` |
| GET    | `/health/live`  | Process is up (no dependency checks)                       |
| GET    | `/health/ready` | Database, RabbitMQ (publisher) and the event consumer OK (200), otherwise 503 |

What `POST /orders` does (step 4, with events):

1. `GET /carts/{userId}` on cart. Cart down → 503 `CART_UNAVAILABLE`; empty → 409 `CART_EMPTY`.
2. Saves the order as `PENDING`, copying title, author and price from the cart.
3. Publishes `OrderCreated` (`orderId`, `userId`, `items[{bookId, qty}]`) to the
   `chapter-one.events` exchange, routing key `OrderCreated` (`app/events.py`).
   - published → 202 with the `PENDING` order
   - RabbitMQ down → order stays `PENDING`, 503 `BROKER_UNAVAILABLE` with the `orderId`
     in `details` (nothing re-sends it yet: the outbox in step 5 fixes that)

The event carries the request's `X-Correlation-ID` if it's a UUID, otherwise a new one.

## Events

Besides the API, order runs a consumer (`app/consumer.py`) that finishes orders when
inventory answers:

| Queue                  | Event in        | Order becomes                  | Event out (same correlationId)                     |
|------------------------|-----------------|--------------------------------|----------------------------------------------------|
| `order.stock-reserved` | `StockReserved` | `CONFIRMED`                    | `OrderConfirmed` (orderId, userId, totalMinor, currency) |
| `order.stock-failed`   | `StockFailed`   | `CANCELLED` (`cancelReason` = the event's reason) | `OrderCancelled` (orderId, userId, reason) |

- Only a `PENDING` order changes. The same answer again → nothing changes, the
  follow-up event is published again (the first may have been lost).
- Answer contradicting the order's status → logged as an error, nothing published.
- Unreadable message or unknown order id → dropped with an error log. Database or
  broker trouble → put back in the queue after 2s.
- Messages are acked only after the follow-up event is published.

Budgets: 1.5s for the cart call (retries included, up to 3 attempts when there's no
answer; `app/clients.py`) and 1.5s for publishing (`app/events.py`); why:
`docs/decisions/0002-timeout-budgets.md`. The cart is not emptied yet: cart will do that
itself when it hears `OrderConfirmed` (step 4 piece 5).

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
| `RABBITMQ_URL` | (required) | `amqp://user:pass@rabbitmq:5672/`                        |

## Run locally

Tests marked `db` / `rabbitmq` start a throwaway Postgres / RabbitMQ with
testcontainers, so **Docker must be running**. Skip them with
`pytest -m "not db and not rabbitmq"`. The code imports `shared/` from the repo root
(`pythonpath` in `pyproject.toml`); in Docker, Compose passes `shared/` to the build as
an extra build context.

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
