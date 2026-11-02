# CLAUDE.md — Chapter One

This file gives Claude (and humans) the context needed to work on this repository.
Read it fully before making changes. If a request conflicts with the rules here, point out the conflict before proceeding.

## Project purpose

**Chapter One** is a learning project: a small online bookstore built as microservices.

## Naming

- Display name (UI, docs, page titles): **Chapter One**
- Code name (everything technical): `chapter-one`
  - Repository: `chapter-one`
  - Docker images: `chapter-one/<service>` (e.g. `chapter-one/catalog`, `chapter-one/order`)
  - Docker Compose project name: `chapter-one`
  - Kubernetes namespace: `chapter-one`
  - Helm chart (later): `chapter-one`
  - Kubernetes labels: `app.kubernetes.io/part-of: chapter-one`, `app.kubernetes.io/name: <service>`
- RabbitMQ exchange: `chapter-one.events`; queues: `<consumer-service>.<event>` (e.g. `inventory.order-created`)
- Databases: `<service>_db` (e.g. `catalog_db`, `order_db`)
- Code name is always lowercase with hyphens; never use spaces, underscores or capitals in image, namespace or chart names.
It runs locally with Docker Compose first, and will later be deployed to a Kubernetes cluster.

The goal is to **learn microservices patterns properly**, so prefer clear, explicit code over clever abstractions,
and explain non-obvious design decisions in comments or PR descriptions.

## Tech stack

See `docs/decisions/0001-python-fastapi-stack.md` for the reasoning.

- Language / framework: Python 3.12, FastAPI + Uvicorn, pydantic-settings for config
- Libraries: SQLAlchemy 2.0 async + asyncpg, Alembic (migrations), redis-py, aio-pika, httpx
- Tooling: pytest, ruff; dependencies via `requirements.txt` per service
- Databases: PostgreSQL (catalog, order, inventory), Redis (cart)
- Message broker: RabbitMQ
- Local runtime: Docker + Docker Compose
- Later: Kubernetes (manifests or Helm), OpenTelemetry + Jaeger for tracing

Unless told otherwise, use the same language and framework for all services so patterns stay consistent.

## Architecture

```
Frontend
   │
API Gateway  (routing, JWT validation, correlation ID)
   │
   ├── Catalog   (PostgreSQL)   books, authors, prices
   ├── Cart      (Redis)        temporary user carts, TTL ~7 days
   └── Order     (PostgreSQL)   orders + status, saga coordinator
          │
          ▼  publishes events
     RabbitMQ
          │
          ├── Inventory     (PostgreSQL)  stock counts, reservations
          └── Notification  (no DB)       logs "fake emails"
```

- **Synchronous** (REST): Frontend → Gateway → Catalog / Cart / Order.
- **Asynchronous** (events via RabbitMQ): Order ↔ Inventory, Order → Notification.

### Service ownership

| Service      | Owns                                  | Storage    | Talks to                      |
|--------------|---------------------------------------|------------|-------------------------------|
| gateway      | routing, auth check, correlation IDs  | none       | all HTTP services             |
| catalog      | books, prices, search                 | PostgreSQL | nobody (others call it)       |
| cart         | items in a user's cart                | Redis      | catalog (price/existence)     |
| order        | orders, status, checkout saga         | PostgreSQL | cart (HTTP), RabbitMQ         |
| inventory    | stock levels, reservations            | PostgreSQL | RabbitMQ only                 |
| notification | sending (logging) user notifications  | none       | RabbitMQ only                 |

## Checkout flow (saga)

1. `POST /orders` → Order reads the cart, **copies prices into the order**, saves it as `PENDING`.
2. Order publishes `OrderCreated`.
3. Inventory consumes it and reserves stock atomically:
   - success → publishes `StockReserved`
   - failure → publishes `StockFailed`
4. Order consumes the reply → status becomes `CONFIRMED` or `CANCELLED`, then publishes `OrderConfirmed` / `OrderCancelled`.
5. Notification consumes `OrderConfirmed` / `OrderCancelled` and logs an email. The cart is cleared on confirmation.

Order statuses: `PENDING` → `CONFIRMED` | `CANCELLED`. No other transitions.

## Events

All events share an envelope:

```json
{
  "eventId": "uuid",
  "eventType": "OrderCreated",
  "occurredAt": "2026-01-01T12:00:00Z",
  "correlationId": "uuid",
  "version": 1,
  "payload": { }
}
```

| Event           | Producer  | Consumers            | Payload (minimum)                         |
|-----------------|-----------|----------------------|-------------------------------------------|
| OrderCreated    | order     | inventory            | orderId, userId, items[{bookId, qty}]     |
| StockReserved   | inventory | order                | orderId                                   |
| StockFailed     | inventory | order                | orderId, reason                           |
| OrderConfirmed  | order     | notification, cart   | orderId, userId, total                    |
| OrderCancelled  | order     | notification         | orderId, userId, reason                   |

When adding or changing an event, update this table. Breaking payload changes require bumping `version`.

## Hard rules (do not break these)

1. **No shared databases.** A service may only read/write its own datastore. Other services' data is obtained via their API or via events. Never add a connection string for another service's DB.
2. **Copy data you need to keep.** Orders store price and title at checkout time; they never look up current catalog prices for historical orders.
3. **Consumers must be idempotent.** RabbitMQ is at-least-once. Track processed `eventId`s (e.g. a `processed_events` table) or make the operation naturally safe to repeat.
4. **Use the outbox pattern for publishing** (once step 5 of the roadmap is reached): write the event to an `outbox` table in the same DB transaction as the state change; a separate worker publishes it. Never "save, then publish" as two unrelated steps in final code.
5. **Atomic stock updates.** Reserve stock with a conditional update (e.g. `UPDATE ... SET qty = qty - :n WHERE book_id = :id AND qty >= :n`) and check the affected row count. No read-then-write.
6. **Every outbound HTTP call has a timeout** (default 2s) and a limited retry with backoff for idempotent requests. Never wait indefinitely.
7. **Propagate the correlation ID.** The gateway creates `X-Correlation-ID` if missing; every service forwards it on HTTP calls, puts it in every event envelope, and includes it in every log line.
8. **Auth lives at the gateway.** The gateway validates the JWT and forwards `X-User-ID`. Services trust that header and do not re-implement login.

## Kubernetes readiness (apply from day one)

- All configuration comes from **environment variables** (DB URL, Redis URL, broker URL, ports). No hardcoded hosts or secrets. Provide a `.env.example`.
- Each HTTP service exposes:
  - `GET /health/live` — process is up
  - `GET /health/ready` — dependencies (DB, broker) reachable
- Log **structured JSON to stdout**. No log files, no writing to local disk.
- Services are stateless; state lives only in their datastore.
- Handle `SIGTERM` gracefully: stop accepting requests, finish in-flight work, close connections.
- One Dockerfile per service, multi-stage, small runtime image, runs as non-root.

## Repository layout

```
chapter-one/
├── CLAUDE.md
├── docker-compose.yml
├── .env.example
├── services/
│   ├── gateway/
│   ├── catalog/
│   ├── cart/
│   ├── order/
│   ├── inventory/
│   └── notification/
├── frontend/
├── shared/            # ONLY event envelope/schema definitions and tiny helpers; no business logic
├── deploy/
│   └── k8s/           # added later
└── docs/
    ├── progress.md    # where the project stands
    ├── decisions/     # short ADRs for notable design choices
    └── learning/      # plain-language guide per roadmap step
```

Each service folder contains its own source, tests, Dockerfile, migrations (if it has a DB) and a short README with its endpoints.

Do not put business logic in `shared/`. Sharing domain code couples services together.

## API conventions

- REST + JSON, resource-oriented paths, plural nouns: `/books`, `/carts/{userId}/items`, `/orders/{id}`.
- Gateway exposes everything under `/api/...` and strips the prefix when routing.
- Errors use a consistent shape: `{ "error": { "code": "BOOK_NOT_FOUND", "message": "..." } }`.
- Use proper status codes (400 validation, 404 not found, 409 conflict, 503 dependency unavailable).
- Money is stored as integer minor units (e.g. cents) plus a currency code, never floats.
- IDs are UUIDs.

## Roadmap (build in this order)

1. Catalog service alone, with PostgreSQL, running in Docker Compose.
2. API gateway + Cart service (Redis).
3. Order service calling Inventory **synchronously** (intentionally simple and fragile).
4. Replace that call with events via RabbitMQ (the saga above).
5. Outbox pattern, idempotent consumers, Notification service.
6. Correlation IDs end-to-end, health checks, structured logging verified.
7. Kubernetes manifests (Deployments, Services, Ingress, ConfigMaps/Secrets, HPA for catalog).
8. Optional: OpenTelemetry tracing, Jaeger, Prometheus metrics.

When asked to build something, check which roadmap step it belongs to. Don't introduce later-stage tools (Kafka, service mesh, Kubernetes) early unless explicitly asked.

## How Claude should work in this repo

- At the start of a session, read `docs/progress.md` to see where the project stands. At the end of each piece of work, update it (status table, decisions, open questions).
- At the end of each roadmap step, write a plain-language learning guide in `docs/learning/step-N-<topic>.md`: what was built, how a request flows through it, why each piece was built that way, where to find things, and a few self-check questions with answers. See `docs/learning/step-1-catalog.md` for the format.
- Before writing code, briefly state which service(s) are affected and whether any hard rule is involved.
- Keep changes scoped to the service being worked on. If a change requires touching another service's contract (API or event), say so explicitly and update the docs here.
- Add or update tests with every change: unit tests for logic, and at least one test covering the happy path of new endpoints or consumers.
- When introducing a pattern (saga, outbox, idempotency, retries), add a short explanation in a comment or in `docs/decisions/`, since this is a learning project.
- Prefer simple, readable solutions over frameworks or abstractions that hide what's happening.
- Don't add new infrastructure (databases, brokers, caches) without asking.
- Keep `docker-compose.yml` working after every change; `docker compose up --build` should start the whole system.

## Commands

```bash
docker compose up --build        # start everything
docker compose down -v           # stop and remove volumes
kubectl get pods -n chapter-one  # later, on Kubernetes

# per service (run inside services/<service>/ with its .venv active)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest                           # tests
ruff check . && ruff format --check .   # lint
```
