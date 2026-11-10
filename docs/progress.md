# Progress log

Where the project stands and what comes next. Updated at the end of every piece of work.
Roadmap steps are defined in `CLAUDE.md`; each step is built in small pieces.

## Current status

- **Roadmap step:** 2 in progress (API gateway + Cart service with Redis)
- **Next piece:** step 2, piece 7 — learning guide `docs/learning/step-2-gateway-cart.md`
- **Last updated:** 2026-11-10

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
| 6  | JWT validation at the gateway, forwards `X-User-ID`                   | done    |           |
| 7  | Learning guide `docs/learning/step-2-gateway-cart.md`                 | todo    |           |

## Decisions made along the way

Bigger choices go in `docs/decisions/` as ADRs; smaller ones are noted here.

- Python/FastAPI stack — see `docs/decisions/0001-python-fastapi-stack.md`.
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
