# Progress log

Where the project stands and what comes next. Updated at the end of every piece of work.
Roadmap steps are defined in `CLAUDE.md`; each step is built in small pieces.

## Current status

- **Roadmap step:** 1 — Catalog service alone, with PostgreSQL, in Docker Compose
- **Next piece:** 6 — Catalog book API
- **Last updated:** 2026-10-31

## Step 1 — Catalog service

| #  | Piece                                                                 | Status  | Commit    |
|----|-----------------------------------------------------------------------|---------|-----------|
| 1  | Stack decision (ADR 0001), `.gitignore`, `.env.example`               | done    | `a272bf3` |
| 2  | Minimal FastAPI service, env config, `/health/live`, first test       | done    | `d202e84` |
| 3  | Multi-stage non-root Dockerfile, `docker-compose.yml`                 | done    | `0b0c58d` |
| 4  | Postgres (`catalog_db`), SQLAlchemy async engine, `/health/ready`     | done    | `39729de` |
| 5  | Alembic migrations, `books` table, seed data, migrate on startup      | done    | `5c1ecf5` |
| 6  | `GET /books` (search, pagination), `GET /books/{id}`, `POST /books`, DB-backed tests | next |  |
| 7  | Structured JSON logging (planned)                                     | planned |           |

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
- Migrations run in the container entrypoint. **Must move to a Kubernetes Job or
  initContainer in roadmap step 7** (multiple replicas would race).

## Open questions / planned

- Piece 6 plans to use **testcontainers** (throwaway Postgres per test run) for
  DB-backed tests. Not yet confirmed.
- Structured JSON logs: CLAUDE.md requires them from day one, but logs are still
  uvicorn/alembic plain text. Planned as piece 7 of step 1.
- Local dev machine needs `sudo apt install python3.12-venv` to create virtualenvs;
  until then tests run in a throwaway `python:3.12-slim` container.
