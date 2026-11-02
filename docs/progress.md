# Progress log

Where the project stands and what comes next. Updated at the end of every piece of work.
Roadmap steps are defined in `CLAUDE.md`; each step is built in small pieces.

## Current status

- **Roadmap step:** 2 in progress (API gateway + Cart service with Redis)
- **Next piece:** step 2, piece 2 — gateway forwards `/api/books...` to catalog
- **Last updated:** 2026-11-02

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
| 1  | Gateway skeleton: health checks, JSON logs, error shape, Dockerfile, Compose | done |       |
| 2  | Gateway forwards `/api/books...` to catalog (timeout, retry on GET, 503 if down) | todo |  |
| 3  | Cart service skeleton with Redis (health checks, Compose)             | todo    |           |
| 4  | Cart endpoints (add/remove/list, 7-day TTL), book check + price via catalog | todo |        |
| 5  | Gateway forwards `/api/carts...` to cart                              | todo    |           |
| 6  | JWT validation at the gateway, forwards `X-User-ID`                   | todo    |           |
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
- Gateway listens on port 8000 (catalog 8001; later services continue 8002, 8003...).
- Migrations run in the container entrypoint. **Must move to a Kubernetes Job or
  initContainer in roadmap step 7** (multiple replicas would race).

## Open questions / planned

- Correlation IDs in log lines: the JSON format is ready (add a `correlationId` field);
  wiring it up is roadmap step 6.
- `POST /books` has no auth yet; the gateway (step 2) will decide who may create books.
