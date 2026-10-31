# catalog

Owns books, authors and prices. Other services call it; it calls nobody.

## Endpoints

| Method | Path           | Description                      |
|--------|----------------|----------------------------------|
| GET    | `/health/live` | Process is up (no dependency checks) |
| GET    | `/health/ready`| Database reachable (200), otherwise 503 |
| GET    | `/books`       | List books, sorted by title. Query: `q` (search title/author), `limit` (1–100, default 20), `offset` |
| GET    | `/books/{id}`  | One book; 404 `BOOK_NOT_FOUND` if missing |
| POST   | `/books`       | Create a book; 201 + `Location` header |

JSON fields are camelCase. Example book:

```json
{ "id": "11111111-1111-4111-8111-000000000001", "title": "Pride and Prejudice",
  "author": "Jane Austen", "priceMinor": 999, "currency": "EUR",
  "createdAt": "...", "updatedAt": "..." }
```

`POST /books` body: `title`, `author`, `priceMinor` (integer cents), `currency` (e.g. `EUR`).

Errors look like `{"error": {"code": "...", "message": "..."}}`: 400 `VALIDATION_ERROR`
(with `details` per field), 404, and 503 `DATABASE_UNAVAILABLE` if the DB is down.

## Configuration (env vars)

| Variable       | Default    | Description                                              |
|----------------|------------|----------------------------------------------------------|
| `PORT`         | `8001`     | HTTP port                                                |
| `LOG_LEVEL`    | `INFO`     | Log level                                                |
| `DATABASE_URL` | (required) | `postgresql+asyncpg://user:pass@host:5432/catalog_db`    |

## Run locally

Tests marked `db` (`tests/test_books_api.py`) start a throwaway Postgres container with
testcontainers, so **Docker must be running**. The others need nothing.
Run only the fast ones with `pytest -m "not db"`. To run the app itself you need
Postgres, so the simplest way is Docker (below).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest
ruff check . && ruff format --check .
```

## Database migrations

Alembic, hand-written migrations in `migrations/versions/`. The container runs
`alembic upgrade head` on startup (`docker-entrypoint.sh`). `0002` seeds 6 demo books
with fixed ids `11111111-1111-4111-8111-00000000000N` (N = 1..6).

```bash
docker compose exec catalog alembic current            # applied revision
docker compose exec catalog alembic downgrade -1       # undo the last one
```

New migration: add `migrations/versions/000N_<name>.py` with `revision = "000N"` and
`down_revision` pointing at the previous one, then rebuild.

## Run with Docker

From the repo root: `docker compose up --build catalog`. The image is `chapter-one/catalog`
and runs as non-root (UID 10001).
