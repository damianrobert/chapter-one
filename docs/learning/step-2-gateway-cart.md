# Step 2 — API gateway and Cart: what was built and why

A plain-language guide to roadmap step 2, written at the end of the step (2026-11-10).
Read it to refresh the big picture before working on later steps. It builds on
`step-1-catalog.md`, so things explained there (Docker, health checks, JSON logs) are
only mentioned briefly here.

**Contents**

1. [The new idea: services calling each other](#part-1-the-new-idea-services-calling-each-other)
2. [What step 2 built](#part-2-what-step-2-built), following one request from start to finish
3. [What happens when something breaks](#part-3-what-happens-when-something-breaks)
4. [Known weak spots](#part-4-known-weak-spots)
5. [Where to find things](#part-5-where-to-find-things)
6. [Check yourself](#part-6-check-yourself), with answers

---

## Part 1: The new idea: services calling each other

In step 1 there was one service, so nothing could go wrong *between* services. Now there
are three, and they phone each other:

```
   You (curl, later the frontend)
          │
       Gateway  :8000     the front door. No data of its own.
       │     │
       │     └──► Cart :8002 ──► Redis      what's in each user's cart
       │            │
       ▼            ▼ "does this book exist, what does it cost?"
     Catalog :8001 ──► Postgres             books and prices
```

Inside one program, calling a function basically always works. A call across the network
can do three other things:

- **Fail**: the other service is switched off.
- **Hang**: the other service is frozen, and you wait with no answer.
- **Get lost halfway**: the other service did the work, but you never heard back.

Most of step 2 is about those three cases. The bookstore comparison: when you phone
another department, you don't stay on hold all day. You give up after a moment, maybe try
once or twice more, then tell the customer "sorry, try again later."

---

## Part 2: What step 2 built

**Two new services:** the gateway and the cart, plus the cart's own Redis.

### Following one request from start to finish

Say a logged-in user puts two copies of a book in their cart:
`PUT /api/carts/<me>/items/<book>` with `{"quantity": 2}`.

```
you ──► GATEWAY
          1. logs the start time
          2. checks your login token                 no or bad token → 401
          3. is this YOUR cart?                      someone else's → 403
          4. removes "/api" from the path, adds X-User-ID
          │
          ▼
        CART
          5. checks the input (quantity 1–99, ids are UUIDs)
          6. phones catalog: "tell me about this book"
          │     └─► CATALOG reads Postgres, answers  not found → 404
          7. one-currency-per-cart check             different currency → 409
          8. saves the item in Redis, restarts the 7-day timer
          9. reads the cart back and answers with it
          │
you ◄─────┘  200 + the whole cart
```

On a running system this took about 25 milliseconds. Now each piece.

---

### 1. The gateway: one front door

**Files:** `services/gateway/app/routes.py`, `app/proxy.py`

**What:** The gateway looks at the start of the path and passes the request on.
`/api/books...` goes to catalog, `/api/carts...` goes to cart, with `/api` removed.

**Why it's built this way:**

- **The frontend only needs one address.** Services can be moved or split later without
  the frontend noticing.
- **Common jobs happen once.** Checking logins here means catalog, cart and order don't
  each need their own copy of that code.
- **It doesn't know each service's endpoints.** It only knows the prefix. If cart adds a
  new endpoint, the gateway needs no change. The service itself answers "not found."
- **Only chosen headers are passed along** (`content-type`, `accept`). Copying everything
  would also copy details about your connection to the gateway, which are wrong for the
  gateway's own call.
- **It adds `/api` back to a `Location` header.** Catalog says a new book lives at
  `/books/<id>`, because catalog has never heard of `/api`. The client needs
  `/api/books/<id>`.

### 2. Timeouts and retries: not waiting forever

**File:** `services/gateway/app/proxy.py` (and `services/cart/app/catalog_client.py`)

**What:** Every call gets 2 seconds. Calls that only read are tried up to 3 times, with a
short pause that grows each time (0.2s, then 0.4s).

**Why:**

- **Without a timeout,** one frozen service makes every caller wait too. Waiting requests
  pile up until the callers fall over as well. One sick service takes down the whole shop.
- **Only reading is retried.** If a call fails, you can't tell whether the other side did
  the work. Reading twice is harmless. Creating a book twice is not.
- **The pause grows** so that a service that's restarting gets a moment to recover instead
  of being hammered.

### 3. Login: the tamper-proof name badge

**Files:** `services/gateway/app/auth.py`, `app/routes.py`

**What:** A **JWT** is like a name badge saying "this is user X, valid until 3pm," sealed
with a signature only the gateway can make. You show it with every request:
`Authorization: Bearer <token>`.

| Request                     | Who may do it                           |
|-----------------------------|-----------------------------------------|
| Read books                  | Anyone                                  |
| Add a book                  | Any logged-in user (no admin role yet)  |
| Use a cart                  | Only the logged-in owner of that cart   |

**Why it's built this way:**

- **Anyone can read a badge, but changing it breaks the signature.** So never put secrets
  in a token.
- **Only the gateway holds the secret** (`JWT_SECRET`). It's hard rule 8: auth lives at
  the gateway. The gateway tells services who you are with an `X-User-ID` header.
- **A client can't fake `X-User-ID`.** The gateway sets that header itself and never
  copies one sent by the client.
- **401 and 403 mean different things.** 401 is "I don't know who you are." 403 is "I know
  who you are, and you're not allowed."

There's no real login page yet. For testing, `python -m app.dev_token <uuid>` makes a
badge that lasts one hour.

### 4. The cart and Redis: a fast, forgetful notebook

**Files:** `services/cart/app/carts.py`, `app/redis_client.py`

**What:** Redis keeps data in memory, so it's very fast. Each cart is one entry
(`cart:<userId>`) holding one line per book:

```
cart:33333333-…  →  11111111-…  →  {"title": "Pride and Prejudice", "priceMinor": 999,
                                    "currency": "EUR", "quantity": 2, ...}
```

**Why it's built this way:**

- **Redis instead of Postgres.** A cart is temporary and changes often. Losing one would
  be annoying but not a disaster. Orders are the opposite, so they'll use Postgres.
- **The 7-day timer deletes old carts by itself.** Every change restarts it. Nobody has to
  write a clean-up job.
- **Saving and restarting the timer happen together** (a Redis "transaction"), so a cart
  can never end up saved with no timer and stay forever.
- **`PUT` sets the quantity to 2. It doesn't "add 2."** Sending the same request twice gives
  the same result, so repeating it is safe. The technical word for this is *idempotent*,
  and it matters a lot from step 4 onwards.

### 5. Asking catalog, and keeping a copy

**File:** `services/cart/app/catalog_client.py`

**What:** Before saving, the cart asks catalog's public API about the book, then **copies**
the title and price into the cart.

**Why:**

- **The cart never opens catalog's database** (hard rule 1). It asks, like anyone else.
- **The copy means showing a cart doesn't need catalog at all.** Only changing a quantity
  does, so people can still see their cart while catalog is down.
- **The trade-off:** if catalog changes a price later, the cart's copy is out of date until
  the next change to that item. That's accepted for now and written down in
  `docs/progress.md`.

### 6. What the services copied from step 1

Dockerfile pattern, settings from environment variables, health checks, JSON logs and the
error shape all came straight from catalog. Step 1's template paid off here. One new twist:
**the gateway's `/health/ready` doesn't check the other services.** If it did, a broken
catalog would make the gateway "not ready," and Kubernetes would stop sending it any
traffic, so carts would die too.

---

## Part 3: What happens when something breaks

These results come from running the real system and breaking it on purpose.

| Catalog is switched off                 | Result                                  | Time  |
|-----------------------------------------|-----------------------------------------|-------|
| Browse books                            | 503 `SERVICE_UNAVAILABLE` (from gateway) | 0.6s |
| Look at your cart                       | 200, works normally                     | fast  |
| Change a quantity                       | 503 `CATALOG_UNAVAILABLE` (from cart)   | 0.6s  |

Look at the 0.6 seconds: that's the pauses between tries (0.2s + 0.4s). And notice the
middle row. **Part of the shop kept working while another part was down.** That's the
payoff for splitting things up and copying the data you need.

---

## Part 4: Known weak spots

Nothing here needs fixing today, but each is worth understanding.

**1. A frozen catalog gives a confusing answer.** A *switched-off* catalog is handled well.
A *frozen* one is different, and testing it showed this:

- Cart waits up to 2 seconds per try for catalog, and tries 3 times.
- The gateway only waits 2 seconds for cart. It gives up first and answers
  `503 SERVICE_UNAVAILABLE`, "the cart service is unavailable."
- That message blames the wrong service. Cart is fine.
- Cart **keeps working after the gateway has given up**. In the test, catalog woke up, cart
  saved the item, and the customer had already been told it failed.

This is the third failure from Part 1, "got lost halfway," happening for real. It's harmless
here because `PUT` is safe to repeat. A retrying customer ends up with the same cart. It
would be serious for something like taking a payment, which is a big reason step 4 moves
checkout to events. The usual fix is making sure an inner call's total time fits inside the
outer call's timeout.

**2. Catalog and cart can be reached without the gateway.** Compose opens their ports on your
computer, so `localhost:8002` skips the login check. Fine for local testing. In step 7, only
the gateway will be reachable from outside.

**3. There's no real login yet.** Only the dev command makes tokens.

**4. Requests can't be followed across services yet.** Each service logs its own lines, with
nothing linking them. Correlation IDs arrive in step 6.

---

## Part 5: Where to find things

```
services/gateway/app/
├── routes.py            which path goes where, and who may use it   ← start here
├── proxy.py             forwarding: timeout, retries, 503
├── auth.py              checking and making login tokens
├── dev_token.py         `python -m app.dev_token <uuid>`
└── main.py              startup/shutdown, health checks, request logging

services/cart/app/
├── carts.py             the cart endpoints and how Redis stores them  ← start here
├── catalog_client.py    asking catalog about a book
├── redis_client.py      connecting to Redis
├── schemas.py           shape of JSON in and out
└── main.py              startup/shutdown, health checks, request logging
```

Try the whole thing yourself (from the repo root):

```bash
docker compose up --build -d

ME=33333333-3333-4333-8333-000000000001
BOOK=11111111-1111-4111-8111-000000000001
TOKEN=$(docker compose exec -T gateway python -m app.dev_token $ME)

# Anyone can browse
curl "localhost:8000/api/books?q=austen"

# No badge: 401
curl -i localhost:8000/api/carts/$ME

# Put two copies in your cart
curl -X PUT localhost:8000/api/carts/$ME/items/$BOOK \
  -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"quantity": 2}'

# Look inside Redis: the cart, and the seconds left on its timer
docker compose exec cart-redis redis-cli HGETALL cart:$ME
docker compose exec cart-redis redis-cli TTL cart:$ME

# Break it: browsing fails, your cart still works
docker compose stop catalog
curl localhost:8000/api/books
curl -H "Authorization: Bearer $TOKEN" localhost:8000/api/carts/$ME
docker compose start catalog

docker compose down
```

---

## Part 6: Check yourself

Try answering before you open the answers.

1. Why does the gateway retry a `GET` but not a `POST`?
2. Why can you still see your cart when catalog is down, but not change a quantity?
3. What's the difference between a 401 and a 403?
4. What stops someone sending their own `X-User-ID` header to pretend to be another user?
5. Why is the cart's `PUT` "set quantity to 2" rather than "add 2"?
6. Why doesn't the gateway's `/health/ready` check catalog and cart?

<details>
<summary>Answers</summary>

1. When a call fails, you can't tell whether the other side already did the work. Repeating
   a `GET` only reads again, so it's harmless. Repeating a `POST` might create the same book
   twice.
2. The cart keeps its own copy of the title and price, so showing a cart only needs Redis.
   Changing a quantity asks catalog whether the book exists and what it costs now, so that
   needs catalog to answer.
3. **401**: "I don't know who you are" (no token, or a bad one). **403**: "I know who you
   are, and this isn't yours."
4. The gateway only passes on a short list of headers, and `X-User-ID` isn't one of them. It
   writes that header itself from the checked token. (Going straight to the cart's own port
   skips this, which is why only the gateway will be reachable from outside in step 7.)
5. So a repeat is harmless. If the response gets lost and the request is sent again, the
   quantity is still 2. With "add 2" it would become 4.
6. If it did, one broken service would make the gateway "not ready," and Kubernetes would
   stop sending it any traffic, including requests for the healthy services. Instead the
   gateway stays up and answers 503 only for what's actually broken.

</details>
