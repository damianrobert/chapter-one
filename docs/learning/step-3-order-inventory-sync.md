# Step 3 — Order and Inventory, talking directly: what was built and why

A plain-language guide to roadmap step 3, written at the end of the step (2026-11-11).
It builds on `step-1-catalog.md` and `step-2-gateway-cart.md`. Things explained there
(Docker, health checks, JSON logs, the gateway, timeouts and retries) are only mentioned
briefly here.

Step 3 is **meant to be fragile**. It builds checkout the simple way, with order phoning
inventory and waiting for an answer, so we can see exactly where that hurts. Step 4
replaces the phone call with messages. Part 4 of this guide is the main reason the step
exists.

**Contents**

1. [The new idea: one action, several services](#part-1-the-new-idea-one-action-several-services)
2. [What step 3 built](#part-2-what-step-3-built), following one checkout from start to finish
3. [Time limits that fit inside each other](#part-3-time-limits-that-fit-inside-each-other)
4. [Breaking it on purpose](#part-4-breaking-it-on-purpose)
5. [Known weak spots, and why step 4 exists](#part-5-known-weak-spots-and-why-step-4-exists)
6. [Where to find things](#part-6-where-to-find-things)
7. [Check yourself](#part-7-check-yourself), with answers

---

## Part 1: The new idea: one action, several services

Until now, every request changed data in **one** place. Putting a book in your cart only
changed Redis. Checkout is different: one click has to change data in two services that
don't share a database.

- **Order** must write down "this customer bought these books at these prices."
- **Inventory** must take those copies off the shelf so nobody else gets them.

If this were one program with one database, both changes would go in one transaction:
either both happen, or neither does. With two services and two databases, **there's no
such transaction.** Each service can only promise things about its own data. Most of
step 3 is about what that means when things go wrong halfway.

The bookstore comparison: the till (order) and the stockroom (inventory) are in different
buildings. The cashier writes up the sale, then phones the stockroom: "put two Draculas
aside for sale #123." Usually that works. Part 4 is about the times it doesn't.

```
   You
    │
 Gateway :8000 ─────────────────────────────┐
    │              │                         │
  Catalog :8001   Cart :8002 ── Redis      Order :8004 ── Postgres (order_db)
    │              ▲                         │    │
  Postgres         └──── "what's in it?" ────┘    │ "reserve these books"
                                                  ▼
                                           Inventory :8003 ── Postgres (inventory_db)
```

---

## Part 2: What step 3 built

**Two new services:** inventory and order, each with its own Postgres database. Plus one
new route in the gateway, `/api/orders`.

### Following one checkout from start to finish

A logged-in user with one Dracula in their cart sends `POST /api/orders` (no body: you
order whatever is in your cart).

```
you ──► GATEWAY            checks login, adds X-User-ID          no login → 401
          │
          ▼
        ORDER
          1. asks CART: "what's in this user's cart?"       cart down → 503
                                                            cart empty → 409
          2. saves the order as PENDING, with a COPY of
             each book's title, author and price
          3. asks INVENTORY: "reserve these for order #123"
          │     └─► INVENTORY: one transaction, for each book:
          │           "take N copies, but only if at least N are left"
          4. reserved            → CONFIRMED
             not enough copies   → CANCELLED (reason INSUFFICIENT_STOCK)
             no answer           → stays PENDING, you get 503
          │
you ◄─────┘  201 + the order
```

On the running system this took about a tenth of a second. Now each piece.

---

### 1. Inventory: counting copies without overselling

**Files:** `services/inventory/app/reservations.py`, `app/stock.py`

**What:** A `stock` table with one row per book: how many copies are on the shelf.
`PUT /reservations/{orderId}` takes copies off for an order.

**Why it's built this way:**

- **"Check and take" happen in one step (hard rule 5).** The database runs:

  ```sql
  UPDATE stock SET quantity = quantity - 1
  WHERE book_id = <Dracula> AND quantity >= 1
  ```

  and then checks: "did that change one row?" The tempting version is: read the count in
  Python, check it's enough, then write the new count. That breaks when two customers
  arrive at the same moment. Both read "2 left," both think "fine," and you sell 4 copies
  of a book you have 2 of. Part 4 tests exactly this.
- **All books or none.** An order for three books runs in one database transaction. If the
  third book is out of stock, the first two are put back automatically.
- **Books are always handled in the same order** (sorted by id). Otherwise two orders could
  each grab one book and wait forever for the other's. That's called a *deadlock*.
- **Asking twice is harmless (idempotent).** The order id is stored with the reservation. If
  the same order asks again, inventory says "already done" (200) instead of taking more
  copies. That's what makes it safe for order to retry (section 4).
- **No link to catalog's books.** `book_id` is just a copied id, not a database link to
  catalog's table (hard rule 1: no shared databases).

### 2. Order: write it down first, and keep a copy

**Files:** `services/order/app/orders.py`, `migrations/versions/0001_create_orders.py`

**What:** Two tables. `orders` has the status, the total and who ordered. `order_items`
has one line per book, with the title, author and price **copied** from the cart.

**Why it's built this way:**

- **The copy is the receipt (hard rule 2).** If catalog changes Dracula's price next week,
  your order still says what you actually paid. Order never looks up a price again for an
  old order. (Bonus: in Part 4, ordering still works while catalog is switched off.)
- **Save PENDING *before* calling inventory.** Inventory files the reservation under the
  order's id, so the id has to exist first. And if something fails halfway, the order is
  at least written down, not silently lost.
- **Only three statuses, and only two moves:** `PENDING → CONFIRMED` or
  `PENDING → CANCELLED`. The database itself refuses any other status, and the update says
  `WHERE status = 'PENDING'`, so a finished order can never change again.
- **Order adds up the total itself**, from the lines it copied. It doesn't trust the cart's
  total, so the stored total always matches the stored lines.
- **You only see your own orders.** The gateway passes your id in `X-User-ID` (hard rule 8).
  Someone else's order id answers 404, the same as an id that doesn't exist, so nobody can
  find out which order ids exist by guessing.

### 3. Not enough copies is a normal answer, not an error

When inventory says "not enough copies," the order becomes `CANCELLED`, and you still get
**201**. An order *was* created; its `status` tells you how it went. Something going wrong
in the shop (sold out) is different from something going wrong in the system (a service
is down). Only the second gives you an error code.

### 4. Retrying the reservation safely

**File:** `services/order/app/clients.py`

If order gets no answer from inventory, it tries again, up to 3 times in total. That's only
safe because of inventory's "asking twice is harmless" rule from section 1. Without it, a
retry after a lost answer would take the copies twice.

---

## Part 3: Time limits that fit inside each other

**Files:** `services/gateway/app/routes.py` (`BUDGETS`), `docs/decisions/0002-timeout-budgets.md`

Step 2 found a problem (its Part 4, weak spot 1): the gateway gave up after 2 seconds, but
cart kept waiting on catalog for up to 6.6 seconds. Checkout made it worse. Order asks cart,
*then* inventory, which could add up to about 13 seconds behind a 2-second gateway.

Two rules fixed it:

1. **A time limit covers the whole call, retries included.** "2 seconds" now really means
   "you have an answer within about 2 seconds." Quick failures still get retried. A frozen
   service uses up the time once and isn't waited for again.
2. **Whoever asks waits longer than whoever they asked could take.** Limits shrink as a
   request goes deeper:

| Who waits for whom                               | Limit |
|--------------------------------------------------|-------|
| cart → catalog, order → cart, order → inventory  | 1.5s  |
| gateway → catalog, gateway → cart                | 2s    |
| gateway → order                                  | 5s    |

Order gets 5 seconds because it makes two calls one after the other. That's the price of a
chain of phone calls: **every link adds waiting time for the customer.**

---

## Part 4: Breaking it on purpose

These results come from the running system on 2026-11-11, using
`docker compose stop` (switched off) and `docker compose pause` (frozen: still there,
but not answering).

| What broke                     | What the customer got                        | Time  | What was stored                  |
|--------------------------------|----------------------------------------------|-------|----------------------------------|
| Nothing                        | 201 `CONFIRMED`                              | ~0.1s | order CONFIRMED, 1 copy taken    |
| Catalog switched off           | 201 `CONFIRMED`                              | 0.09s | same; order doesn't need catalog |
| Cart switched off              | 503 `CART_UNAVAILABLE`                       | 0.6s  | nothing                          |
| Inventory switched off         | 503 `INVENTORY_UNAVAILABLE`                  | 0.65s | order **PENDING**, no copy taken |
| Inventory's database off       | 503 `INVENTORY_UNAVAILABLE`                  | 0.9s  | order **PENDING**, no copy taken |
| Inventory **frozen**, then unfrozen | 503 `INVENTORY_UNAVAILABLE`             | 1.5s  | order **PENDING**, but a copy **was** taken ⚠ |

### The rush: 6 customers, 2 copies, same moment

Six users each had one Dracula in their cart (2 in stock) and pressed "order" at the same
time. Result: **exactly 2 `CONFIRMED`, 4 `CANCELLED`, stock 0.** Nothing was oversold. That
is the "check and take in one step" rule from Part 2 doing its job.

### The important one: the frozen inventory

Look at the last row of the table again. Here's what happened, step by step:

1. Order saved the order as PENDING and sent "reserve 1 Dracula for order #c130…"
2. Inventory was frozen. The request arrived and waited in line, unanswered.
3. After 1.5 seconds, order gave up and told the customer "inventory is unavailable."
4. Inventory was unfrozen. It found the waiting request and **reserved the copy.**
5. Nobody was listening anymore. The order stays PENDING forever, and one Dracula sits in
   the stockroom set aside for a sale that, as far as the customer knows, failed.

This is the third failure from step 2's Part 1, *"got lost halfway,"* and this time it
does real damage. Order and inventory now disagree, and nothing in the system will ever
notice or fix it. Counting the stuck orders after these experiments: 5 orders were PENDING
in `order_db`.

Order is *right* not to mark such an order CANCELLED. It doesn't know whether the copy was
taken. But PENDING-forever isn't an answer either.

### Ordering twice from the same cart

Pressing "order" twice gives **two confirmed orders**, because the cart isn't emptied after
checkout yet. With 2 copies in stock and 1 in the cart, both went through.

---

## Part 5: Known weak spots, and why step 4 exists

**1. Stuck PENDING orders (the big one).** When order doesn't hear back from inventory, the
answer is lost for good. Nobody asks again later. **Step 4's fix:** instead of phoning and
waiting, order leaves a message ("OrderCreated") in a mailbox (RabbitMQ). Inventory reads
it whenever it can and leaves a reply ("StockReserved" or "StockFailed"). A message waits
in the mailbox until it's handled, so an answer can arrive late, but it can't get lost.

**2. The customer waits for everyone.** Checkout is only as fast as the slowest service, and
only works if *all* of them are up right now. With messages, order can say "got it, we're
processing it" straight away, and inventory being down for a minute just means a slower
confirmation.

**3. Saving the order, then calling inventory, are two separate steps.** If order crashes
in between, the order is saved but inventory is never asked. Step 5's *outbox* fixes this:
the order and the message to send are saved in one database transaction.

**4. The cart isn't emptied after checkout,** so ordering twice orders twice. In CLAUDE.md's
design, cart empties itself when it hears "OrderConfirmed" (step 4/5). A double click
could still make two orders; an "idempotency key" could stop that later.

**5. Copies are never given back.** There's no "release reservation" yet. It will be needed
once an order can be cancelled after its copies were reserved.

**6. Order and inventory can be reached directly** on ports 8004 and 8003, skipping the
gateway's login check. Fine for local testing. In step 7, only the gateway will be
reachable from outside.

---

## Part 6: Where to find things

```
services/inventory/app/
├── reservations.py      reserving stock: one step, all-or-nothing, safe to repeat  ← start here
├── stock.py             GET/PUT /stock/{bookId}: look at or set the count
└── tables.py            stock, reservations, reservation_items

services/order/app/
├── orders.py            POST/GET /orders: the checkout flow       ← start here
├── clients.py           phoning cart and inventory: time limits, retries
├── schemas.py           shape of JSON going out
└── tables.py            orders, order_items, the three statuses

services/gateway/app/routes.py      /api/orders route and the time limits (BUDGETS)
docs/decisions/0002-timeout-budgets.md   why the time limits are what they are
```

Try it yourself (from the repo root):

```bash
docker compose up --build -d

ME=$(python3 -c 'import uuid; print(uuid.uuid4())')
DRACULA=11111111-1111-4111-8111-000000000005
TOKEN=$(docker compose exec -T gateway python -m app.dev_token $ME)
AUTH="Authorization: Bearer $TOKEN"

# Put a Dracula in your cart, then order it
curl -X PUT localhost:8000/api/carts/$ME/items/$DRACULA -H "$AUTH" \
  -H 'content-type: application/json' -d '{"quantity": 1}'
curl -X POST localhost:8000/api/orders -H "$AUTH"
curl localhost:8000/api/orders -H "$AUTH"

# How many copies are left? (inventory isn't behind the gateway; ask it directly)
curl localhost:8003/stock/$DRACULA

# The lost answer: freeze inventory, order, unfreeze, then compare
docker compose pause inventory
curl -X POST localhost:8000/api/orders -H "$AUTH"      # 503 after 1.5s
docker compose unpause inventory
curl localhost:8003/stock/$DRACULA                     # a copy was taken anyway
curl localhost:8000/api/orders -H "$AUTH"              # ...but the order says PENDING

# Put the demo stock back (Dracula starts with 2)
curl -X PUT localhost:8003/stock/$DRACULA -H 'content-type: application/json' \
  -d '{"quantity": 2}'
```

---

## Part 7: Check yourself

Try answering before you open the answers.

1. Why can't order and inventory simply use one database transaction for checkout?
2. Two customers want the last copy at the same moment. What stops both getting it?
3. Why does the order save prices from the cart instead of asking catalog later?
4. Why does order save the order as PENDING *before* calling inventory?
5. Why is it safe for order to retry the reservation call, but it wouldn't be for a
   "take one copy" call without an order id?
6. Inventory didn't answer in time. Why does order leave the order PENDING instead of
   cancelling it?
7. Why does the gateway wait 5 seconds for order, but only 2 for cart?
8. What will messages (step 4) change about the frozen-inventory problem?

<details>
<summary>Answers</summary>

1. Each service has its own database (hard rule 1), and a database transaction only covers
   one database. No single transaction spans `order_db` and `inventory_db`.
2. The database does "check there's at least one, and take it" as one step:
   `UPDATE ... WHERE quantity >= 1`. The second customer's update finds 0 and changes no
   rows, so their order is cancelled. The rush test showed exactly 2 of 6 orders confirmed
   for 2 copies.
3. An order is a record of what was actually bought and paid. If catalog changes a price
   later, old orders must not change. Also, order can't read catalog's database, and
   doesn't need catalog to be up to show an order.
4. Inventory files the reservation under the order's id, so the id must exist first. And if
   anything fails afterwards, the order is still on record instead of vanishing.
5. Inventory remembers which order already reserved what. Asking again with the same order
   id gets "already done," not a second copy. A plain "take one copy" call has no way to
   tell a retry from a new request, so a retry after a lost answer would take two copies.
6. Order doesn't know what happened. Inventory might have reserved the copies and only the
   answer got lost (the frozen test showed exactly that). Cancelling would leave copies
   locked away for a "failed" order. PENDING honestly means "we don't know yet."
7. Cart only makes one call of its own (to catalog, max 1.5s). Placing an order makes two
   calls one after the other (cart, then inventory, max 1.5s each) plus database writes. A
   caller must wait longer than everything behind it might take.
8. The request becomes a message that waits in RabbitMQ until inventory handles it, and
   inventory's reply is a message too. A slow or frozen inventory delays the answer instead
   of losing it, so the order eventually becomes CONFIRMED or CANCELLED.

</details>
