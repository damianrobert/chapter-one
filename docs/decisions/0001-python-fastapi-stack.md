# 0001 — Python + FastAPI for all services

- Status: accepted
- Date: 2026-10-31

## Context

Chapter One is a learning project. All six services should use the same language and
framework so patterns (outbox, idempotent consumers, retries) look the same everywhere
and the focus stays on microservices concepts, not on switching tools.

## Decision

| Concern            | Choice                                   | Why                                                      |
|--------------------|------------------------------------------|----------------------------------------------------------|
| Language           | Python 3.12                              | Readable, little boilerplate                             |
| HTTP framework     | FastAPI + Uvicorn                        | Explicit routes, request validation via Pydantic, async  |
| Config             | pydantic-settings                        | Typed config read from environment variables             |
| PostgreSQL access  | SQLAlchemy 2.0 (async) + asyncpg         | Explicit transactions, which the outbox pattern needs    |
| Migrations         | Alembic                                  | Standard for SQLAlchemy; versioned schema per service    |
| Redis (cart)       | redis-py (async)                         | Official client                                          |
| RabbitMQ           | aio-pika                                 | Async AMQP client with explicit acks                     |
| Outbound HTTP      | httpx                                    | Async, supports per-request timeouts                     |
| Tests              | pytest + httpx test client               | Simple, widely used                                      |
| Lint / format      | ruff                                     | One fast tool for both                                   |
| Dependencies       | pip + `requirements.txt` per service     | No extra tooling to learn; works the same in Docker      |

Each service is its own Python project with its own `requirements.txt`, so services
do not share installed code. `shared/` only holds event envelope definitions.

## Consequences

- Larger Docker images than Go; mitigated with multi-stage builds on `python:3.12-slim`.
- Async everywhere: DB, Redis, broker and HTTP calls must not block the event loop.
