# Step 1 — Catalog service: what was built and why

A plain-language guide to roadmap step 1, written at the end of the step (2026-11-02).
Read it to refresh the big picture before working on later steps.

**Contents**

1. [What "microservices" means](#part-1-what-microservices-means)
2. [What step 1 built](#part-2-what-step-1-built), following one request from start to finish
3. [Why so much care for one small service?](#part-3-why-so-much-care-for-one-small-service)
4. [Where to find things](#part-4-where-to-find-things)
5. [Check yourself](#part-5-check-yourself), with answers

---

## Part 1: What "microservices" means

### The idea

Picture a real bookstore. One person could do everything: shelve books, run the till,
track stock, send receipts. That works while the shop is small. As it grows, that one
person becomes a bottleneck, and if they're off sick, the whole shop closes.

So you split the work into **departments**:

- **Catalog desk**: knows every book and its price
- **Shopping baskets**: holds what each customer is about to buy
- **Orders desk**: handles the checkout
- **Stockroom**: knows how many copies are on the shelf
- **Mailroom**: sends confirmation emails

Each department has **its own filing cabinet** and nobody else opens it. If the orders
desk needs a price, it *asks* the catalog desk. It doesn't go rummaging in the catalog's
drawers.

That's microservices. Each department is a small, separate program (a **service**) with
its own data, and they cooperate by sending each other messages.

The opposite is a **monolith**: one big program with one big database. Monoliths are
simpler and often the right choice. Microservices pay off when you need parts that can
be changed, deployed and scaled independently. This project builds them to *learn* the
patterns, not because a bookstore strictly needs them.

### The trade-off, honestly

| You gain                                                      | You pay                                                  |
|---------------------------------------------------------------|----------------------------------------------------------|
| Each service can be changed and deployed alone                | Many programs to run, watch and debug                    |
| A crash in one service needn't take down the rest             | Network calls can fail, be slow or arrive twice          |
| Each part can be scaled separately (e.g. more catalog copies) | Data is spread out, so "join" queries across services aren't possible |

Almost every rule in `CLAUDE.md` exists to handle something in the right-hand column.

### How Chapter One's departments will talk

```
            Browser
               │
         API Gateway  ← the front door: checks who you are, forwards requests
               │
     ┌─────────┼──────────┐
  Catalog     Cart      Order         ← "phone calls" (HTTP): ask and wait for an answer
 (Postgres)  (Redis)  (Postgres)
                          │
                      RabbitMQ        ← the "mailbox": drop a note, carry on
                          │
              ┌───────────┴─────────┐
          Inventory           Notification
          (Postgres)           (no database)
```

There are two ways services talk:

1. **Synchronous (HTTP)**: like a phone call. "What's the price of book X?" You wait for
   the answer. It's simple, but if the other side is down, you're stuck.
2. **Asynchronous (events via RabbitMQ)**: like leaving a note in a shared mailbox.
   "Order 42 was created." Whoever cares picks it up when they can. Neither side waits
   for the other. This comes in step 4.

---

## Part 2: What step 1 built

**Only the catalog service, plus its own database.** Nothing else exists yet. That's on
purpose: the first service is where every pattern gets decided (config, Docker, health
checks, errors, logging, tests). The other five services will copy them.

### Following one request from start to finish

Say you run `curl localhost:8001/books?q=austen`. Here's everything that happens, in order:

```
docker compose up
   │
   ├─ 1. starts catalog-db (Postgres) and waits until it answers
   │
   └─ 2. starts the catalog container
           │
           ├─ 3. docker-entrypoint.sh runs migrations (creates/updates tables)
           ├─ 4. python -m app → sets up JSON logging → starts uvicorn (web server)
           └─ 5. FastAPI app starts: creates the database connection pool
                                    │
   your request ──────────────────► 6. logging middleware starts a timer
                                    7. router finds list_books() for GET /books
                                    8. FastAPI checks the inputs (q, limit, offset)
                                    9. code builds SQL, borrows a DB connection, runs it
                                   10. the result is turned into JSON (camelCase)
                                   11. middleware logs "request completed", 200, 19ms
   ◄──────────────────────────────  response
```

Now each piece, with what it does and why it was built that way.

---

### 1. Docker and Docker Compose: the "shipping container"

**Files:** `services/catalog/Dockerfile`, `docker-compose.yml`

**What:** A Docker *image* is a sealed box holding the program plus everything it needs:
Python, libraries, code. A *container* is that box running. Compose starts several
containers together and connects them on a private network, where they find each other
by name (the catalog reaches its database at `catalog-db`).

**Why it's built this way:**

- **Multi-stage build.** Stage 1 installs the libraries. Stage 2 copies *only the result*
  into a clean, small image. The installation tools are left behind, so the image is
  smaller and has less to attack.
- **Runs as a non-root user (UID 10001).** If someone ever broke into the program, they
  wouldn't have admin rights inside the container. Kubernetes can enforce this rule later.
- **`depends_on: service_healthy`.** Compose waits for Postgres before starting the
  catalog. That's a local convenience only. Kubernetes has no equivalent, so the service
  *also* copes with the database being down by itself (see health checks below).
- **The database has no port open to your computer.** Only the catalog can reach it.
  That's the hard rule **"no shared databases"** made physical: other services literally
  can't connect.

### 2. Configuration from environment variables

**Files:** `app/config.py`, `.env.example`

**What:** Settings like the database address, port and log level aren't written into the
code. They're passed in from outside as *environment variables*.

**Why:** The **same image** must run unchanged on your laptop and later in Kubernetes.
Only the settings around it change. `pydantic-settings` checks them at startup.
`DATABASE_URL` has **no default on purpose**: if it's missing, the service crashes
immediately with a clear error instead of quietly connecting to the wrong place.

### 3. The service's own database and migrations

**Files:** `migrations/versions/0001_create_books.py`, `0002_seed_books.py`,
`docker-entrypoint.sh`

**What:** A *migration* is a numbered script that changes the database's structure:
"0001: create the books table," "0002: add 6 demo books." Alembic (the migration tool)
tracks which ones have run, and the container runs any new ones each time it starts.

**Why:**

- The database structure is **versioned like code**. Anyone can rebuild the exact same
  database from scratch, and `downgrade` can undo a change.
- **Database-level rules** (price ≥ 0, currency is 3 capital letters) protect the data
  even if a bug in the code tries to save something bad.
- One known issue is written down in `docs/progress.md`: running migrations at startup is
  fine with one copy of the service. With several copies in Kubernetes, they'd all try to
  migrate at once. That moves to a separate job in step 7.

### 4. Health checks: `/health/live` and `/health/ready`

**File:** `app/main.py`

These look similar, but the difference matters a lot in Kubernetes:

|                 | Question it answers                 | Checks the DB? | If it fails, Kubernetes…               |
|-----------------|-------------------------------------|----------------|----------------------------------------|
| `/health/live`  | "Is the program running at all?"    | **No**         | **restarts** the container             |
| `/health/ready` | "Can I handle real work right now?" | **Yes**        | **stops sending traffic** (no restart) |

**Why separate them:** if the database goes down, restarting the catalog won't fix it.
You just want it to stop receiving traffic until the database is back. If "live" checked
the database, Kubernetes would keep restarting perfectly healthy containers for no reason.

This is also why the service **starts even when the database is down**. It just reports
"not ready" until it can reach it.

### 5. The web layer: FastAPI, routes and validation

**Files:** `app/books.py`, `app/schemas.py`

**What:** `books.py` defines three endpoints:

- `GET /books`: search and page through books
- `GET /books/{id}`: one book
- `POST /books`: create a book

`schemas.py` describes the exact **shape** of the JSON going in and out.

**Why it's built this way:**

- **Validation happens before your code runs.** If someone sends `priceMinor: 9.99` or
  `/books/abc`, FastAPI rejects it with a 400 automatically. `extra="forbid"` means a typo
  like `"price"` is reported as an error instead of being silently ignored.
- **Money is stored as whole cents** (`999` = €9.99) **plus a currency code.** Computers
  can't store decimals like 0.1 exactly (`0.1 + 0.2 ≠ 0.3`), and money must add up
  perfectly.
- **IDs are UUIDs** (random 128-bit IDs like `11111111-1111-4111-…`) rather than 1, 2, 3.
  Any service can generate one without asking a central counter, and they don't reveal
  how many books you have.
- **Pagination with a maximum of 100** stops anyone from asking for the whole table in
  one go.
- **Search input is "escaped" and sent as a parameter,** never pasted into the SQL text.
  That blocks *SQL injection*, a classic attack where user input gets run as database
  commands.
- **camelCase JSON** (`priceMinor`) matches the event format coming in step 4. The whole
  system then uses one style.

### 6. Talking to the database: SQLAlchemy Core

**Files:** `app/db.py`, `app/tables.py`

**What:** The *engine* holds a **pool** of open database connections. Each request
borrows one and hands it back afterwards, so a few connections can serve many requests.

**Why:**

- **"Core" instead of the "ORM."** An ORM hides SQL behind Python objects. Core keeps the
  SQL visible (`select(books).where(...)`), which helps you see what's actually happening.
  That matters in a learning project.
- **A 2-second timeout** when connecting. The rule is "never wait forever." This matters
  much more once services start calling each other.
- **`pool_pre_ping`** checks that a pooled connection still works before using it. That
  covers the case where Postgres restarted overnight.

### 7. Consistent errors

**File:** `app/errors.py`

**What:** Every error, whether ours or FastAPI's, looks the same:

```json
{ "error": { "code": "BOOK_NOT_FOUND", "message": "Book … not found" } }
```

**Why:** Other services and the frontend will read these errors. A fixed `code` lets
them write `if code == "BOOK_NOT_FOUND"` without parsing messages. The status codes mean
specific things:

- **400**: you sent something wrong
- **404**: it doesn't exist
- **503**: *my* dependency (the database) is down, so try again later

That last one tells callers it's not their fault and retrying makes sense.

### 8. Structured JSON logging

**Files:** `app/logging_config.py`, the `log_requests` middleware in `app/main.py`

**What:** Every log line is one JSON object, written to the screen (stdout), never to a
file:

```json
{"timestamp": "2026-11-02T08:06:54.721Z", "level": "INFO", "service": "catalog",
 "logger": "app.requests", "message": "request completed", "method": "GET",
 "path": "/books", "status": 200, "durationMs": 19.0}
```

**Why:** With six services, you'll want to ask "show me every error across all services
around 14:02." Labelled fields make that a filter instead of a text search. Stdout is
used because containers are throwaway and the platform collects their output. Health
checks are logged at DEBUG level so the every-10-seconds probes don't drown out real
traffic.

In step 6, each line also gets a `correlationId`: one ID that follows a single customer
action through *every* service. It's how you'll trace one checkout across five programs.

### 9. Tests

**Folder:** `services/catalog/tests/`

There are two kinds:

- **Fast tests** (health, validation, logging) that need nothing running.
- **Database tests** (marked `db`). These start a **throwaway Postgres container** with
  *testcontainers*, run the real migrations on it, test against it, then delete it. Your
  real local data is never touched, and you're testing against real Postgres, not a fake.

Run everything with `pytest`, or only the fast ones with `pytest -m "not db"`.

### 10. Graceful shutdown

When Docker or Kubernetes wants to stop a container, it sends a polite "please stop"
signal (SIGTERM). Uvicorn then stops taking new requests, finishes the ones in progress,
and the app closes its database connections (the `lifespan` function in `app/main.py`).
The `exec` in `docker-entrypoint.sh` and the JSON-array `CMD` in the Dockerfile make sure
Python actually *receives* that signal. In the logs, a clean stop ends with
"Shutting down" and then "Application shutdown complete."

---

## Part 3: Why so much care for one small service?

Most of what was built isn't about books. It's the **template**:

| Built in step 1                           | Reused by                                           |
|-------------------------------------------|-----------------------------------------------------|
| Dockerfile pattern, non-root, multi-stage | every service                                       |
| Env-var config, no hardcoded hosts        | every service, and Kubernetes ConfigMaps in step 7  |
| `/health/live` and `/health/ready`        | every service, and Kubernetes probes in step 7      |
| Error shape and status codes              | the gateway relies on it                            |
| JSON logging                              | correlation IDs in step 6                           |
| Own database plus migrations              | order and inventory                                 |

In step 2 (gateway and cart) you'll recognise most of the structure. The new things will
be **services calling each other over the network**: timeouts, retries, and what to do
when the other side is down. That's where microservices start feeling different from a
normal app.

---

## Part 4: Where to find things

```
services/catalog/
├── Dockerfile               how the image is built (multi-stage, non-root)
├── docker-entrypoint.sh     runs migrations, then starts the app
├── alembic.ini              migration tool settings
├── migrations/versions/     0001 creates the books table, 0002 adds demo books
├── app/
│   ├── __main__.py          `python -m app`: sets up logging, starts uvicorn
│   ├── main.py              the FastAPI app: startup/shutdown, health checks, request logging
│   ├── config.py            settings read from environment variables
│   ├── db.py                database connection pool and ping
│   ├── tables.py            Python description of the books table (for building queries)
│   ├── schemas.py           shape of JSON in and out, plus validation rules
│   ├── books.py             the /books endpoints
│   ├── errors.py            the standard error format
│   └── logging_config.py    JSON log formatting
└── tests/                   fast tests and real-Postgres tests
```

Useful commands (from the repo root):

```bash
docker compose up --build                                   # start everything
docker compose logs catalog --no-log-prefix                 # see the JSON logs
docker compose exec catalog-db psql -U catalog -d catalog_db  # look inside the database
docker compose down -v                                      # stop and delete data
```

---

## Part 5: Check yourself

Try answering before you open the answers.

1. Why can't the order service just connect to `catalog_db` to look up a price?
2. If Postgres goes down, which health check fails, and what will Kubernetes do?
3. Why is €9.99 stored as `999`?
4. What's the difference between a "phone call" (HTTP) and a "mailbox" (event) between
   services?

<details>
<summary>Answers</summary>

1. **Hard rule 1: no shared databases.** If order read catalog's tables directly, the
   catalog could never change its table structure without breaking order. The two
   services would be tied together, and that defeats the point of separate services.
   Order must ask the catalog's API (or, later, receive events). In this project it
   doesn't even need to: it copies the price from the cart at checkout time (hard rule 2).
2. **`/health/ready`** returns 503. Kubernetes **stops sending traffic** to that container
   but doesn't restart it. `/health/live` still says OK, because the program itself is
   fine. Once the database is back, "ready" turns OK and traffic resumes.
3. Computers store decimals like 9.99 as approximations, so adding many of them can be off
   by a fraction of a cent. Whole numbers are always exact. The currency code (`EUR`) says
   what the number means.
4. **Phone call (HTTP):** the caller waits for an answer. It's simple and immediate, but
   if the other side is slow or down, the caller is stuck too. **Mailbox (event):** the
   sender drops a message and carries on. The receiver handles it whenever it can, even
   if it was down for a while. Neither side has to be online at the same moment. The
   price is that things happen "eventually" rather than instantly.

</details>
