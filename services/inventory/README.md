# inventory

Owns stock levels (how many copies of each book can still be sold). It knows book ids
only — titles and prices belong to catalog.

It reserves stock when it receives an `OrderCreated` event and answers with
`StockReserved` or `StockFailed` (the checkout saga in `CLAUDE.md`, roadmap step 4).

## Events

| Direction | Event           | Queue / routing key                      | Payload                    |
|-----------|-----------------|------------------------------------------|----------------------------|
| in        | `OrderCreated`  | queue `inventory.order-created` (durable) | `orderId, userId, items[{bookId, qty}]` |
| out       | `StockReserved` | routing key `StockReserved`              | `orderId`                  |
| out       | `StockFailed`   | routing key `StockFailed`                | `orderId, reason` (`INSUFFICIENT_STOCK`) |

How it works (`app/consumer.py`): reserve, publish the answer (same `correlationId`),
then ack. A repeated `OrderCreated` gets the same answer again without touching stock.
Unreadable messages are dropped with an error log; temporary trouble (DB/broker down)
puts the message back after 2s. The consumer connects in the background and retries
every 3s, so inventory starts even while RabbitMQ is down.

## Endpoints

| Method | Path                       | Description                                         |
|--------|----------------------------|-----------------------------------------------------|
| GET    | `/health/live`             | Process is up (no dependency checks)                |
| GET    | `/health/ready`            | Database reachable and consumer connected to RabbitMQ (200), otherwise 503 |
| GET    | `/stock/{bookId}`          | `{bookId, quantity}`; 404 `STOCK_NOT_FOUND` if no record |
| PUT    | `/stock/{bookId}`          | Body `{"quantity": N}` (0–1,000,000): **sets** the count (creates the record if needed) |
| PUT    | `/reservations/{orderId}`  | Body `{"items": [{"bookId", "quantity"}]}`: reserve all items or none |

`PUT /reservations/{orderId}`:

- **201**: reserved; the copies are taken out of stock.
- **200**: this order already had exactly this reservation; nothing changes (safe retry).
- **409 `INSUFFICIENT_STOCK`**: some book is short (or has no stock record). Nothing
  is reserved; `details` names the book (first attempt only). The failure is
  **remembered**: the same order id gets the same answer later, even after new stock
  arrives (needed for repeated events; migration `0004`).
- **409 `RESERVATION_CONFLICT`**: this order id already reserved *different* items.
- **400 `VALIDATION_ERROR`**: empty list, quantity outside 1–99, a book listed twice, bad id.

How it stays correct under load is explained at the top of `app/reservations.py`
(conditional `UPDATE`, one transaction, order id as primary key).

The gateway doesn't route to inventory. Since step 4 the order service no longer calls it
either (it publishes `OrderCreated` instead; inventory consumes it from piece 3 on). Locally you can
call port 8003 directly, e.g. to stock a book created with `POST /api/books`:

```bash
curl -X PUT localhost:8003/stock/<bookId> -H 'content-type: application/json' -d '{"quantity": 5}'
```

## Data

Table `stock`: `book_id` (catalog's id, no foreign key — that table is in another
database), `quantity` (never below 0, enforced by a DB check), `updated_at`.
Tables `reservations` (`order_id` primary key, `status` `RESERVED`/`FAILED`,
`failure_reason`) and `reservation_items` (`order_id`, `book_id`, `quantity`) record
each order's outcome and what it took (migrations `0003`, `0004`).

Migration `0002` seeds stock for catalog's six demo books: 10 each for books 1–2, 5 for
3–4, **2 for Dracula (…0005)** and **0 for Dorian Gray (…0006)**, so "out of stock" is
easy to try.

```bash
docker compose exec inventory-db psql -U inventory -d inventory_db -c 'select * from stock'
```

## Configuration (env vars)

| Variable       | Default    | Description                                              |
|----------------|------------|----------------------------------------------------------|
| `PORT`         | `8003`     | HTTP port                                                |
| `LOG_LEVEL`    | `INFO`     | Log level (`DEBUG` also shows health-check requests)     |
| `DATABASE_URL` | (required) | `postgresql+asyncpg://user:pass@host:5432/inventory_db`  |
| `RABBITMQ_URL` | (required) | `amqp://user:pass@rabbitmq:5672/`                        |

## Run locally

Tests marked `db` start a throwaway Postgres with testcontainers, so **Docker must be
running**. Skip them with `pytest -m "not db"`.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest
ruff check . && ruff format --check .
```

Migrations work exactly like catalog's (Alembic, run by `docker-entrypoint.sh` on start).

## Run with Docker

From the repo root: `docker compose up --build inventory`. Image `chapter-one/inventory`,
runs as non-root (UID 10001).
