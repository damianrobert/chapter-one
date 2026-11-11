# cart

Holds what each user has put in their cart, in Redis. A cart is deleted automatically
7 days after it was last changed. When a book is put in the cart, cart asks catalog
whether it exists and copies its title and price.

## Endpoints

| Method | Path                                 | Description                                   |
|--------|--------------------------------------|-----------------------------------------------|
| GET    | `/health/live`                       | Process is up                                 |
| GET    | `/health/ready`                      | Redis answers `PING` (503 if not)             |
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

## Configuration (env vars)

| Variable      | Default    | Description                                   |
|---------------|------------|-----------------------------------------------|
| `PORT`        | `8002`     | HTTP port                                     |
| `LOG_LEVEL`   | `INFO`     | Log level                                     |
| `REDIS_URL`   | (required) | e.g. `redis://cart-redis:6379/0`              |
| `CATALOG_URL` | (required) | e.g. `http://catalog:8001`                    |

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
```

## Run locally

Tests marked `redis` (`tests/test_carts_api.py`) start a throwaway Redis container with
testcontainers, so **Docker must be running**. Run only the others with
`pytest -m "not redis"`.

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
