# Progress log

Where the project stands and what comes next. Updated at the end of every piece of work.
Roadmap steps are defined in `CLAUDE.md`; each step is built in small pieces.

## Current status

- **Roadmap step:** 3 in progress (Order service calling Inventory synchronously)
- **Next piece:** step 3, piece 6 — break it on purpose, learning guide `step-3-...md`
- **Last updated:** 2026-11-11

## Step 1 — Catalog service

| #  | Piece                                                                 | Status  | Commit    |
|----|-----------------------------------------------------------------------|---------|-----------|
| 1  | Stack decision (ADR 0001), `.gitignore`, `.env.example`               | done    | `a272bf3` |
| 2  | Minimal FastAPI service, env config, `/health/live`, first test       | done    | `d202e84` |
| 3  | Multi-stage non-root Dockerfile, `docker-compose.yml`                 | done    | `0b0c58d` |
| 4  | Postgres (`catalog_db`), SQLAlchemy async engine, `/health/ready`     | done    | `39729de` |
| 5  | Alembic migrations, `books` table, seed data, migrate on startup      | done    | `5c1ecf5` |
| 6  | `GET /books` (search, pagination), `GET /books/{id}`, `POST /books`, DB-backed tests | done | `5716d1e` |
| 7  | Structured JSON logging (app, uvicorn, alembic), request-log middleware | done  | `6bddb18` |

Learning guide for this step: `docs/learning/step-1-catalog.md`.

## Step 2 — API gateway + Cart service

| #  | Piece                                                                 | Status  | Commit    |
|----|-----------------------------------------------------------------------|---------|-----------|
| 1  | Gateway skeleton: health checks, JSON logs, error shape, Dockerfile, Compose | done | `ec12993` |
| 2  | Gateway forwards `/api/books...` to catalog (timeout, retry on GET, 503 if down) | done | `a4d9f74` |
| 3  | Cart service skeleton with Redis (health checks, Compose)             | done    | `2e9e915` |
| 4  | Cart endpoints (add/remove/list, 7-day TTL), book check + price via catalog | done | `209b2c3` |
| 5  | Gateway forwards `/api/carts...` to cart                              | done    | `2a9a3b7` |
| 6  | JWT validation at the gateway, forwards `X-User-ID`                   | done    | `5ae209d` |
| 7  | Learning guide `docs/learning/step-2-gateway-cart.md`                 | done    |           |

Learning guide for this step: `docs/learning/step-2-gateway-cart.md`.

## Step 3 — Order calls Inventory synchronously

On purpose a simple, fragile design: order waits for inventory's answer over HTTP.
Step 4 replaces that call with events.

| #  | Piece                                                                 | Status  | Commit    |
|----|-----------------------------------------------------------------------|---------|-----------|
| 1  | Inventory skeleton: own Postgres, `stock` table + seed, health, logs, Compose | done | `e4928a2` |
| 2  | Inventory endpoints: get/set stock, reserve stock atomically (hard rule 5) | done | `60f1363` |
| 3  | Order skeleton: own Postgres, `orders` + `order_items` tables         | done    | `c6c4fa1` |
| 4  | `POST /orders`: read cart, copy prices, call inventory, CONFIRMED/CANCELLED; `GET /orders` | done | `235f59f` |
| 5  | Gateway forwards `/api/orders`; settle the timeout budget between services | done    |           |
| 6  | Break it on purpose (pause inventory), learning guide `step-3-...md`  | planned |           |

## Decisions made along the way

Bigger choices go in `docs/decisions/` as ADRs; smaller ones are noted here.

- Python/FastAPI stack — see `docs/decisions/0001-python-fastapi-stack.md`.
- Time budgets between services — see `docs/decisions/0002-timeout-budgets.md`.
- `pip` + `requirements.txt` per service (no uv/poetry); runtime and dev deps split.
- Test client uses `httpx2` (FastAPI's TestClient warns with plain `httpx`).
- Health endpoints return `{"status", "checks"}`, not the API error shape — they're
  read by probes, not API clients.
- Catalog DB has no host port published; inspect with
  `docker compose exec catalog-db psql -U catalog -d catalog_db`.
- Book `author` is a plain column for now (no authors table).
- Seed data lives in migration `0002`, with fixed ids `11111111-1111-4111-8111-00000000000N`.
- Catalog uses SQLAlchemy **Core** (table descriptions + queries), not the ORM, so the
  SQL stays visible. `app/tables.py` describes tables; migrations remain the source of truth.
- API JSON is camelCase (`priceMinor`), matching the event envelope style.
- Validation errors return 400 `VALIDATION_ERROR` with per-field `details`; DB
  unreachable returns 503 `DATABASE_UNAVAILABLE`.
- Book search is a simple case-insensitive `ILIKE` on title/author (fine for a small
  catalog; full-text search could come later).
- DB-backed tests use **testcontainers** (throwaway Postgres, real migrations); marked
  `db`, skip with `pytest -m "not db"`.
- JSON logging uses only the stdlib (`JsonFormatter` in `app/logging_config.py`, no
  extra library). Log field names are camelCase like the API (`durationMs`). Uvicorn's
  text access log is off (`access_log=False`); a middleware logs each request instead.
  Health-check requests log at DEBUG. Tests opt out of Alembic's logging setup via
  `config.attributes["configure_logger"] = False` so pytest keeps capturing logs.
  When more services exist, this file is a candidate for `shared/` (it's a "tiny
  helper", not business logic) — decide then.
- `logging_config.py` is **copied** into each service (not in `shared/`): each image
  builds from its own folder, and copying one small file is simpler than changing
  build contexts. Revisit if the copies start to drift.
- Gateway `/health/ready` does **not** check catalog/cart: otherwise one broken
  service would make the whole gateway "not ready" and cut off every route. The
  gateway answers 503 only on routes whose service is down.
- HTTP client is **`httpx2`** (successor of `httpx`, same API), used both at runtime
  and by FastAPI's TestClient, so services need only one HTTP library. CLAUDE.md's
  stack list still says "httpx"; it means this.
- Gateway forwarding (`services/gateway/app/proxy.py`): 2s timeout per call; retries
  (3 attempts, backoff 0.2s/0.4s) only for GET/HEAD/OPTIONS and only on network
  errors; unreachable → 503 `SERVICE_UNAVAILABLE`; service answers (incl. errors)
  pass through unchanged. Only `content-type`/`accept` go to the service and only
  `content-type`/`location` come back; `Location` gets the `/api` prefix.
- Gateway routes by prefix (`/api/books*` → catalog) without knowing catalog's
  individual endpoints; catalog itself answers 404/405.
- Gateway routes `/api/carts*` → cart the same way, through the same `forward()`, so cart
  gets the same timeout, retry and 503 behaviour. The gateway keeps one retry rule for all
  services: cart's PUT is safe to repeat, but only GET/HEAD/OPTIONS are retried.
- JWTs (`services/gateway/app/auth.py`): PyJWT, HS256 with one shared `JWT_SECRET` (only
  the gateway has it), `exp` and a UUID `sub` required. Bad token → 401 `UNAUTHORIZED`.
- Who may do what: reading books is public; other book methods need any valid login (no
  roles yet). Carts need a login and only your own: the **gateway** compares the path's
  userId with the token (403 `FORBIDDEN`), so cart's API stays unchanged. Paths with `.`
  or `..` segments are refused because httpx2 collapses them before sending.
- The gateway sets `X-User-ID` itself; a client-sent value is never copied.
- `python -m app.dev_token <uuid>` makes one-hour test tokens, since there are no user
  accounts or login endpoint yet.
- Gateway listens on port 8000 (catalog 8001; later services continue 8002, 8003...).
- Cart listens on 8002 and uses its own Redis (`cart-redis`, image `redis:8-alpine`, no
  host port; inspect with `docker compose exec cart-redis redis-cli`). Redis keeps
  snapshots in the `cart-redis-data` volume so carts survive a restart.
- Cart's Redis client: `redis.asyncio` with 2s connect and 2s command timeouts,
  `decode_responses=True`. `/health/ready` checks Redis only (not catalog, same reasoning
  as the gateway).
- Cart health tests use a tiny `FakeRedis` (happy path) and a real client pointed at a
  closed port (503 path). Cart API tests use a **testcontainers Redis** (marker `redis`,
  skip with `-m "not redis"`) and fake catalog with httpx2's `MockTransport`.
- Cart API: `GET /carts/{userId}`, `PUT /carts/{userId}/items/{bookId}` (**sets** the
  quantity 1–99, so repeating it is safe), `DELETE .../items/{bookId}` (204 even if the
  book wasn't there). No "clear cart" endpoint yet: clearing on `OrderConfirmed` comes
  with the events in step 4/5.
- Cart storage: one Redis hash per cart (`cart:<userId>`, field per book holding a JSON
  snapshot of title/author/price/currency + quantity). HSET/HDEL and EXPIRE run together
  in MULTI/EXEC; every change resets the 7-day TTL. Reading doesn't reset it.
- Cart **copies** title and price from catalog on every PUT; it can go stale if catalog
  changes a price later. Accepted for now; order (step 3) copies the cart's price at
  checkout. Revisit if "price changed since you added it" matters.
- One currency per cart: a book in a different currency → 409 `CURRENCY_MISMATCH`, so
  the cart has one `totalMinor` + `currency`. The check is read-then-write; two
  simultaneous requests from the same user could both pass (accepted, single user).
- Cart → catalog calls (`app/catalog_client.py`): 2s timeout, 3 attempts on network
  errors, catalog 404 → cart 404 `BOOK_NOT_FOUND`, any other non-200 or no answer →
  503 `CATALOG_UNAVAILABLE`. GET/DELETE don't call catalog, so they work while it's down.
  Redis unreachable → 503 `DATABASE_UNAVAILABLE` (same code as catalog's DB).
- Inventory listens on 8003 with its own Postgres (`inventory-db`, `inventory_db`); order
  will be 8004. Inventory is a copy of catalog's skeleton (same db.py, errors, logging,
  Alembic setup, Dockerfile).
- `stock` table: one row per book (`book_id` PK, `quantity` with a DB check `>= 0`).
  `book_id` has no foreign key: books live in catalog's DB (hard rule 1). Seed (migration
  `0002`) covers catalog's six demo books; Dracula has 2, Dorian Gray 0, to make "out of
  stock" easy to test. A test compares the seed ids with catalog's.
- Books created later via `POST /books` have no stock row; stock them with
  `PUT /stock/{bookId}` on port 8003 (sets the count, doesn't add to it). The gateway
  doesn't route to inventory, so this is local-only for now.
- Reserving is `PUT /reservations/{orderId}` (`app/reservations.py`): one transaction,
  books updated in sorted id order (avoids deadlocks), each with
  `UPDATE ... WHERE quantity >= :n` and a row-count check (hard rule 5). All or nothing.
- Reservations are **idempotent by order id**: the order id is the primary key of a
  `reservations` table, claimed first with `INSERT ... ON CONFLICT DO NOTHING`. A repeat
  with the same items → 200 and nothing changes; different items → 409
  `RESERVATION_CONFLICT`. This lets order safely retry the call (hard rule 6) and is a
  first taste of step 5's idempotent consumers.
- Short stock and "no stock record" both answer 409 `INSUFFICIENT_STOCK` (with the book
  in `details`). A failed attempt stores nothing, so the same order id can retry later.
- No "release reservation" yet: in step 3 a failed order reserves nothing. Needed once
  orders can be cancelled after reserving.
- Inventory's `ApiError` accepts optional `details` (catalog's and cart's don't need it).
- A test fires 8 simultaneous reservations for Dracula (2 in stock) and checks exactly 2
  succeed.
- Order listens on 8004 with its own Postgres (`order-db`, `order_db`), another copy of
  the same skeleton. Compose doesn't give it cart's/inventory's URLs yet (piece 4).
- `orders`: status limited to `PENDING`/`CONFIRMED`/`CANCELLED` by a DB check;
  `cancel_reason` allowed only on cancelled orders; `total_minor` is BIGINT (a total can
  exceed one INTEGER price); one currency per order (the cart already enforces that).
  Index `(user_id, created_at)` for "my orders, newest first".
- `order_items`: copies `title`, `author`, `unit_price_minor` at checkout (hard rule 2);
  `book_id` is kept for reference but never used to re-read catalog for an old order.
  Primary key `(order_id, book_id)`: one line per book, as in the cart.
- `POST /orders` (`services/order/app/orders.py`) takes no body: it orders whatever is in
  the cart. Order saves `PENDING` first (inventory needs the order id), then calls
  `PUT /reservations/{orderId}`. Reserved → `CONFIRMED`; `INSUFFICIENT_STOCK` →
  `CANCELLED` with that reason; both answer 201 (an order was created either way).
- Inventory unreachable → the order **stays `PENDING`** and the client gets 503
  `INVENTORY_UNAVAILABLE` with the `orderId`. Not cancelled, because inventory may have
  reserved the books and only the answer got lost. Nothing retries it later: such orders
  stay stuck. That's the step-3 weakness piece 6 shows off and step 4 fixes.
- Order works out `total_minor` itself from the copied lines instead of using the cart's
  total. Status changes use `UPDATE ... WHERE status = 'PENDING'`, so a finished order
  never changes again.
- Order → cart/inventory calls (`app/clients.py`): 2s timeout, 3 attempts on no answer.
  Retrying the inventory PUT is safe because inventory is idempotent by order id. Cart
  down → 503 `CART_UNAVAILABLE`; empty cart → 409 `CART_EMPTY` (no order saved).
- Order trusts `X-User-ID`: lists and lookups only show the caller's orders; another
  user's order id answers 404 `ORDER_NOT_FOUND` (same as unknown, so ids can't be probed).
- `GET /orders` loads the items of a whole page in one query (not one query per order).
- The cart is **not** emptied after a confirmed order; per CLAUDE.md that's cart reacting
  to `OrderConfirmed` (step 4/5). Until then, ordering twice orders the same books twice.
- Gateway routes `/api/orders*` → order (login required, `X-User-ID` forwarded). No
  ownership check at the gateway: order paths have no user id in them, so order filters
  by `X-User-ID` itself.
- **Time budgets (ADR 0002):** a timeout now covers the *whole* call, retries included
  (`asyncio.timeout` around the retry loop), and budgets shrink deeper in: cart → catalog,
  order → cart and order → inventory 1.5s each; gateway → catalog/cart 2s, gateway →
  order 5s. Checked 2026-11-11 with `docker compose pause`: frozen inventory → order's own
  503 `INVENTORY_UNAVAILABLE` after 1.5s; frozen catalog → cart's 503
  `CATALOG_UNAVAILABLE` after 1.5s (before: the gateway's generic 503 at 2s).
- Migrations run in the container entrypoint. **Must move to a Kubernetes Job or
  initContainer in roadmap step 7** (multiple replicas would race).

## Open questions / planned

- Correlation IDs in log lines: the JSON format is ready (add a `correlationId` field);
  wiring it up is roadmap step 6.
- Who may create books: any logged-in user for now. An admin role could come later.
- Real login: nothing issues tokens except the dev command. The frontend will need a way
  to log in; user accounts aren't in the roadmap yet.
- Compose publishes catalog's and cart's ports on the host, which skips the gateway's
  login checks. Fine locally; in Kubernetes (step 7) only the gateway should be exposed.
- Cart doesn't forward a correlation ID to catalog yet (roadmap step 6).
- Budgets are kept in sync by hand across services (tests check that they nest). Passing
  a deadline along with each request would automate it; not needed yet.
- Orders left `PENDING` when inventory didn't answer stay stuck forever (nothing
  re-checks them). Step 4's events fix this; until then they can only be spotted by hand.
- Double-clicking "order" places two orders (no idempotency key on `POST /orders`).
  Consider an `Idempotency-Key` header later.
