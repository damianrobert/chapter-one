# inventory

Owns stock levels (how many copies of each book can still be sold). It knows book ids
only — titles and prices belong to catalog.

In roadmap step 3 the order service calls it over HTTP. From step 4 on it talks to
RabbitMQ only (it reserves stock when it receives `OrderCreated`).

## Endpoints

| Method | Path            | Description                               |
|--------|-----------------|-------------------------------------------|
| GET    | `/health/live`  | Process is up (no dependency checks)      |
| GET    | `/health/ready` | Database reachable (200), otherwise 503   |

Stock endpoints (check, reserve) come in the next piece.

## Data

Table `stock`: `book_id` (catalog's id, no foreign key — that table is in another
database), `quantity` (never below 0, enforced by a DB check), `updated_at`.

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
