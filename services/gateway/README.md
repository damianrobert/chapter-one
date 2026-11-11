# gateway

The single front door of Chapter One. The frontend talks only to the gateway; it
routes each request to the right service and checks the user's login (JWT). Later
it also adds a correlation ID. It has no database.

## Endpoints

| Method | Path            | Description                                          |
|--------|-----------------|------------------------------------------------------|
| GET    | `/health/live`  | Process is up                                        |
| GET    | `/health/ready` | Always ready once running (doesn't check other services — see `app/main.py`) |
| any    | `/api/books`, `/api/books/...` | Forwarded to catalog as `/books...` (see catalog's README). Reading is public; other methods need a login |
| any    | `/api/carts/{userId}...` | Forwarded to cart as `/carts...` (see cart's README). Needs a login, and only your own cart |
| any    | `/api/orders`, `/api/orders/...` | Forwarded to order as `/orders...` (see order's README). Needs a login; order only shows your own orders |

The routing table is `app/routes.py`.

## Login tokens

Send a JWT on each protected request: `Authorization: Bearer <token>`. The gateway checks it
(`app/auth.py`) and tells the service who the user is with `X-User-ID`. An `X-User-ID` sent by
the client is ignored.

- No token, or an invalid or expired one: 401 `UNAUTHORIZED`.
- Someone else's cart: 403 `FORBIDDEN`.

There is no login page yet. To get a token that lasts one hour:

```bash
docker compose exec gateway python -m app.dev_token 33333333-3333-4333-8333-000000000001
```

How forwarding works (`app/proxy.py`):
- Every call has a **time budget** covering all attempts: catalog 2s, cart 2s, order 5s
  (`BUDGETS` in `app/routes.py`; why: `docs/decisions/0002-timeout-budgets.md`).
- `GET`/`HEAD`/`OPTIONS` are retried up to 3 attempts on network errors while budget is
  left (pauses 0.2s, 0.4s).
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
| `CART_URL`    | (required) | Cart's address, e.g. `http://cart:8002`   |
| `ORDER_URL`   | (required) | Order's address, e.g. `http://order:8004` |
| `JWT_SECRET`  | (required) | Signs login tokens, at least 32 characters |

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

From the repo root: `docker compose up --build` (the gateway needs catalog and cart). Then
`curl localhost:8000/api/books`. Image `chapter-one/gateway`, runs as non-root (UID 10001).
