# cart

Holds what each user has put in their cart, in Redis. A cart is deleted automatically
7 days after it was last changed. When a book is put in the cart, cart asks catalog
whether it exists and copies its title and price. When an order is confirmed, cart hears
about it (`OrderConfirmed` event) and empties that user's cart.

## Endpoints

| Method | Path                                 | Description                                   |
|--------|--------------------------------------|-----------------------------------------------|
| GET    | `/health/live`                       | Process is up                                 |
| GET    | `/health/ready`                      | Redis answers `PING` and the event consumer is connected (503 if not) |
| GET    | `/carts/{userId}`                    | The cart (empty if the user has none)         |
| PUT    | `/carts/{userId}/items/{bookId}`     | **Set** the quantity of a book: `{"quantity": 2}` (1–99). Returns the cart |
| DELETE | `/carts/{userId}/items/{bookId}`     | Remove a book; 204 (also if it wasn't there)  |

PUT *sets* the quantity, it doesn't add to it, so sending it twice is harmless.
All ids are UUIDs. Example cart:

```json
{ "userId": "aaaaaaaa-aaaa-4aaa-8aaa-000000000001",
  "items": [{ "bookId": "11111111-1111-4111-8111-000000000001",
              "title": "Pride and Prejudice", "author": "Jane Austen",
              "priceMinor": 999, "currency": "EUR", "quantity": 2, "lineTotalMinor": 1998 }],
  "totalMinor": 1998, "currency": "EUR" }
```

`currency` is `null` while the cart is empty. Errors look like
`{"error": {"code": "...", "message": "..."}}`:

| Status | Code                   | When                                                |
|--------|------------------------|-----------------------------------------------------|
| 400    | `VALIDATION_ERROR`     | Bad quantity, unknown field, id not a UUID          |
| 404    | `BOOK_NOT_FOUND`       | Catalog doesn't know the book                       |
| 409    | `CURRENCY_MISMATCH`    | Book's currency differs from the cart's             |
| 503    | `CATALOG_UNAVAILABLE`  | Catalog can't be reached (only PUT needs catalog)   |
| 503    | `DATABASE_UNAVAILABLE` | Redis can't be reached                              |

## How it's stored

One Redis hash per cart: key `cart:<userId>`, one field per book (`<bookId>` → JSON with
title, author, priceMinor, currency, quantity). Every change resets the key's TTL to
7 days. See `app/carts.py` for the reasoning.

## Events

Cart consumes `OrderConfirmed` from queue `cart.order-confirmed` (`app/consumer.py`) and
empties the user's cart. It publishes nothing.

- **Once per order:** a Lua script sets `cart-emptied-for:<orderId>` (kept 7 days) and
  deletes `cart:<userId>` in one atomic step, only if the marker was new. A repeat of the
  same order (even with a new `eventId`) leaves the cart alone, so books added after
  the order survive.
- `OrderCancelled` is ignored: the books stay in the cart so the user can try again.
- Unreadable message → dropped with an error log. Redis down → back in the queue after 2s.

## Configuration (env vars)

| Variable      | Default    | Description                                   |
|---------------|------------|-----------------------------------------------|
| `PORT`        | `8002`     | HTTP port                                     |
| `LOG_LEVEL`   | `INFO`     | Log level                                     |
| `REDIS_URL`   | (required) | e.g. `redis://cart-redis:6379/0`              |
| `CATALOG_URL` | (required) | e.g. `http://catalog:8001`                    |
| `RABBITMQ_URL`| (required) | e.g. `amqp://user:pass@rabbitmq:5672/`        |

Redis calls time out after 2s. Calls to catalog have a 1.5s budget for the whole
call, inside which they're tried up to 3 times on network errors (pauses 0.2s, 0.4s) —
see `app/catalog_client.py` and `docs/decisions/0002-timeout-budgets.md`.

## Inspect the data

Redis has no host port published (only cart may use it). To look inside:

```bash
docker compose exec cart-redis redis-cli
> KEYS cart:*
> HGETALL cart:<userId>
> TTL cart:<userId>
> KEYS cart-emptied-for:*
```

## Run locally

Tests marked `redis` / `rabbitmq` start a throwaway Redis / RabbitMQ container with
testcontainers, so **Docker must be running**. Run only the others with
`pytest -m "not redis and not rabbitmq"`. The code imports `shared/` from the repo root
(`pythonpath` in `pyproject.toml`); in Docker, Compose passes `shared/` to the build as
an extra build context (by hand: `docker build --build-context shared=../../shared .`).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest
ruff check . && ruff format --check .
```

## Run with Docker

From the repo root: `docker compose up --build`. Then e.g.

```bash
curl -X PUT localhost:8002/carts/aaaaaaaa-aaaa-4aaa-8aaa-000000000001/items/11111111-1111-4111-8111-000000000001 \
  -H 'content-type: application/json' -d '{"quantity": 2}'
curl localhost:8002/carts/aaaaaaaa-aaaa-4aaa-8aaa-000000000001
```

Image `chapter-one/cart`, runs as non-root (UID 10001).
