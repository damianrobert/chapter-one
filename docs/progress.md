# Progress log

Where the project stands and what comes next. Updated at the end of every piece of work.
Roadmap steps are defined in `CLAUDE.md`; each step is built in small pieces.

## Current status

- **Roadmap step:** 4 in progress (replace order → inventory call with RabbitMQ events)
- **Next piece:** step 4, piece 5 — cart consumes `OrderConfirmed` and empties the
  user's cart. The saga itself now works end to end: a new order becomes `CONFIRMED` or
  `CANCELLED` within a fraction of a second.
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
| 5  | Gateway forwards `/api/orders`; settle the timeout budget between services | done    | `b7ebb5f` |
| 6  | Break it on purpose (pause inventory), learning guide `step-3-...md`  | done    |           |

Learning guide for this step: `docs/learning/step-3-order-inventory-sync.md` (Part 4 has
the break-it results).

## Step 4 — Checkout via RabbitMQ events (the saga)

Draft plan; adjust as we go. Outbox and Notification are step 5.

| #  | Piece                                                                 | Status  | Commit    |
|----|-----------------------------------------------------------------------|---------|-----------|
| 1  | RabbitMQ in Compose (approved 2026-11-11), event envelope in `shared/` | done |           |
| 2  | Order publishes `OrderCreated`; `POST /orders` answers 202 with a PENDING order (HTTP call to inventory removed) | done | |
| 3  | Inventory consumes `OrderCreated`, reserves, publishes `StockReserved` / `StockFailed`; failed reservations remembered | done | |
| 4  | Order consumes the replies → CONFIRMED / CANCELLED, publishes `OrderConfirmed` / `OrderCancelled` | done | |
| 5  | Cart consumes `OrderConfirmed` and empties the cart                   | planned |           |
| 6  | Decide about inventory's HTTP reservation endpoint, break it again, learning guide `step-4-...md` | planned | |

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
- Step 3 break-it results (2026-11-11, full table in the step-3 guide): catalog off →
  ordering still works; cart off → 503, nothing stored; inventory off/DB off → 503, order
  PENDING; **inventory frozen then unfrozen → order PENDING but the copy *was* reserved**
  (lost answer, nobody notices). 6 simultaneous orders for 2 copies → exactly 2 confirmed.
  Leftover PENDING orders and reservations from these tests are in the local DBs;
  `docker compose down -v` resets everything.
- RabbitMQ (approved by the user 2026-11-11): image `rabbitmq:4-management-alpine`
  (4.4), Compose service `rabbitmq`, fixed `hostname` so its data folder in the
  `rabbitmq-data` volume is found again after a restart. Only the dashboard is published
  (http://localhost:15672, user/password `chapter-one` by default); services will use
  `rabbitmq:5672` inside Compose. One broker user for all services for now.
- Event envelope: `shared/events.py` — a frozen pydantic model `EventEnvelope`
  (camelCase JSON, refuses unknown fields, times without timezone, `version < 1`),
  `new_event()`, `to_json()`/`from_json()`, the exchange name and event type constants.
  The envelope doesn't check the payload; each consumer validates its own.
  `eventId` and `correlationId` are UUIDs.
- `shared/` has its own tests and venv (`cd shared && pytest`). Plan for getting it into
  images: Compose's `build.additional_contexts` hands `shared/` to a service's build
  as an extra folder, so each image still builds from its own folder (piece 2).
- `shared/` reaches order's image through Compose `build.additional_contexts`
  (`shared: ./shared`); the Dockerfile copies only `__init__.py` and `events.py`
  (`COPY --from=shared`). Locally, `pythonpath = [".", "../.."]` in `pyproject.toml`.
  Building an image by hand needs `docker build --build-context shared=../../shared .`.
- Event payloads are pydantic classes in `shared/events.py` too (`OrderCreatedPayload`),
  so producer and consumer read one definition. Payloads **ignore** unknown fields
  (adding a field isn't breaking); the envelope refuses them.
- Order's publisher (`services/order/app/events.py`): aio-pika robust connection, opened
  on first use (order starts and reports "broker unreachable" while RabbitMQ is down,
  reconnects by itself, ~8s after a restart in our test). **Topic** exchange
  `chapter-one.events`, durable; **routing key = event type**. Publisher confirms on,
  persistent messages, `mandatory=True` so an event no queue wants is logged as a
  warning ("event not delivered to any queue") instead of vanishing silently (aio-pika
  doesn't log it by itself). 1.5s budget per publish (ADR 0002 updated).
- **Producers declare only the exchange; each consumer declares and binds its own
  queue** (`<consumer>.<event>`). Events published before a queue exists are lost
  (expected until piece 3).
- `POST /orders` → **202** + `Location`, order `PENDING`, event published after the
  commit. Broker down → 503 `BROKER_UNAVAILABLE` with the `orderId`; the order stays
  `PENDING` with nobody to re-send its event. Save-then-publish is the known gap that
  step 5's outbox fixes (hard rule 4).
- Order's HTTP call to inventory, `INVENTORY_URL` and `InsufficientStock` are gone.
  `finish_order()` stays: piece 4 uses it for the reply events. Inventory's
  `PUT /reservations/{orderId}` still exists (piece 3 reuses its logic).
- Correlation ID for now: order uses `X-Correlation-ID` if it's a UUID, else makes one.
  The gateway doesn't forward that header yet (step 6), so through the gateway it's
  always a fresh one.
- `/health/ready` for order checks database **and** broker (`checks.broker`).
- RabbitMQ 4 refuses non-durable shared queues (`transient_nonexcl_queues` deprecated):
  queues must be durable (or exclusive, as in tests).
- Checked 2026-11-11 against Compose: 202 + PENDING through the gateway; the event caught
  in a temporary queue had the exact CLAUDE.md envelope; RabbitMQ stopped → instant 503
  and ready 503; started again → ready and ordering work without restarting order.
- `shared/messaging.py` (step 4 piece 3): `declare_exchange()`, `to_message()`,
  `log_unroutable()` — the RabbitMQ plumbing every service must do identically, so it's
  shared instead of copied (unlike `logging_config.py`). Needs aio-pika. Payload classes
  for `StockReserved` / `StockFailed` and the reason `INSUFFICIENT_STOCK` are in
  `shared/events.py`. Dockerfiles copy `__init__.py events.py messaging.py`.
- Inventory now **remembers failed reservations** (migration `0004`: `reservations.status`
  `RESERVED`/`FAILED` + `failure_reason`, DB checks). Reason: with at-least-once
  delivery, a repeated `OrderCreated` after a restock would otherwise reserve copies for
  an order already turned down. All-or-nothing now uses a **savepoint**
  (`begin_nested()`) so the FAILED row survives while the stock updates roll back.
  HTTP `PUT /reservations` follows the same rule: a failed order id stays failed
  (step 3's "try again later" test was replaced).
- Inventory's consumer (`services/inventory/app/consumer.py`): durable queue
  `inventory.order-created` bound to `OrderCreated`; prefetch 10; reserve → publish reply
  (with the incoming `correlationId`, publisher confirms, 1.5s budget) → **ack last**. A
  repeat finds the stored outcome and publishes the same answer again (covers "reserved,
  then crashed before answering"). Unreadable message (bad JSON/shape, not v1, >99 copies,
  book twice) → reject without requeue + error log. Other errors → wait 2s, nack with
  requeue. Connects in a background task, retrying every 3s; readiness includes
  `checks.broker` (consumer connected). Shutdown: cancel consumer, wait up to 5s for
  messages in progress, close.
- Inventory's idempotency comes from the reservation keyed by order id (rule 3's
  "naturally safe to repeat"), not a `processed_events` table. Fine while each order
  has exactly one `OrderCreated`.
- Checked 2026-11-11 against Compose: in stock → `StockReserved`, stock −1; Dorian Gray →
  `StockFailed`; inventory stopped while ordering → order still 202, and after starting
  inventory the waiting `OrderCreated` was handled (stock −2). The management API's
  queue counts lag a few seconds, so don't trust "0 waiting" right after publishing.
- Order's consumer (`services/order/app/consumer.py`, step 4 piece 4): two durable
  queues, `order.stock-reserved` and `order.stock-failed` (one per event, per the naming
  rule), on one channel, prefetch 10. Same shape as inventory's consumer: read → finish
  the order → publish the follow-up → ack last; unreadable or **unknown order id** →
  reject without requeue; other errors → wait 2s, nack with requeue.
- `finish_order()` now returns the order (`FinishedOrder`, with `changed`), using
  `UPDATE ... WHERE status = 'PENDING' RETURNING ...`. A repeated answer changes nothing
  and **publishes the follow-up again** (like inventory: the first may have been lost).
  An answer that contradicts the stored status (StockReserved for a CANCELLED order) is
  logged as an error and nothing is published; it can't happen today.
- The consumer publishes through the existing `EventPublisher` (its own connection,
  1.5s budget), so receiving and sending use separate connections.
- `OrderConfirmed` payload: `orderId, userId, totalMinor, currency` (CLAUDE.md said
  "total"; spelled out per the money rule). `OrderCancelled`: `orderId, userId, reason`
  (the reason from `StockFailed`, also stored as `cancel_reason`). Payload classes in
  `shared/events.py`.
- Order's `/health/ready` has a third check, `consumer` (connected and consuming).
- Checked 2026-11-11 against Compose through the gateway: in-stock order → 202 PENDING,
  CONFIRMED ~60ms later; Dorian Gray → CANCELLED `INSUFFICIENT_STOCK`; same
  correlationId on OrderCreated → StockReserved → OrderConfirmed. Orders placed before
  this piece stay PENDING (their answers were dropped: no queue existed yet).
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
- Orders left `PENDING` because the event couldn't be published stay stuck forever
  (nothing re-sends it). Step 5's outbox fixes this.
- Inventory's HTTP `PUT /reservations/{orderId}`: keep it (handy for manual tests) or
  remove it once the consumer exists? Decide in piece 6.
- Unreadable events are dropped (logged). A dead-letter queue would keep them for
  inspection; consider in step 5. Until then such an order stays `PENDING`.
- `OrderConfirmed` / `OrderCancelled` are published but nobody listens until piece 5 /
  step 5 (warnings "event not delivered to any queue" in order's log are expected).
- Consumer plumbing (connect loop, stop, in-progress count) is now copied in inventory
  and order. Cart (piece 5) would make three copies: consider moving it into
  `shared/messaging.py` then.
- A repeated answer re-publishes the follow-up with a **new** `eventId`, so a consumer
  can't spot the repeat by `eventId` alone. Notification (step 5) should dedupe by order
  id + event type, or the outbox can give the repeat the same `eventId`.
- Double-clicking "order" places two orders (no idempotency key on `POST /orders`).
  Consider an `Idempotency-Key` header later.
