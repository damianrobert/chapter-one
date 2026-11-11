# Step 4 — Checkout with messages (the saga): what was built and why

A plain-language guide to roadmap step 4, written at the end of the step (2026-11-11).
It builds on the guides for steps 1–3. Step 3 is the important one to have read: this
step exists to fix the "lost answer" problem shown in its Part 4.

In step 3, order **phoned** inventory and waited on the line. In step 4, services
**leave messages for each other** in a shared mailbox system (RabbitMQ) and get on
with their work. Part 4 of this guide reruns step 3's experiments to show what changed.

**Contents**

1. [The new idea: leave a message instead of phoning](#part-1-the-new-idea-leave-a-message-instead-of-phoning)
2. [What step 4 built](#part-2-what-step-4-built), following one checkout from start to finish
3. [Every message may arrive twice](#part-3-every-message-may-arrive-twice)
4. [Breaking it on purpose (again)](#part-4-breaking-it-on-purpose-again)
5. [Known weak spots, and what step 5 does about them](#part-5-known-weak-spots-and-what-step-5-does-about-them)
6. [Where to find things](#part-6-where-to-find-things)
7. [Check yourself](#part-7-check-yourself), with answers

---

## Part 1: The new idea: leave a message instead of phoning

Back to the bookstore. In step 3 the cashier (order) phoned the stockroom (inventory)
and waited on the line. If nobody picked up, the cashier hung up after 1.5 seconds and
told the customer "sorry, try later". Sometimes the stockroom *had* heard and put the
book aside, but the cashier had already hung up. That was the lost answer.

In step 4 there's a **post room** between them: RabbitMQ.

- The cashier writes a note, "order #123 needs one Dracula", drops it in the post room
  and immediately tells the customer "got it, we're on it".
- The stockroom has its own **mailbox** there. It takes notes out whenever it can. If
  it's closed for an hour, the notes just wait.
- The stockroom answers with a note of its own ("reserved" or "can't"), which goes into
  the cashier's mailbox.

A note can wait, but it can't get lost on the way. That's the whole trick.

The words you'll see in the code:

| Word | Bookstore picture | In Chapter One |
|------|-------------------|----------------|
| **event** | a note saying something *happened* | `OrderCreated`, `StockReserved`, ... |
| **exchange** | the post room's sorting desk | `chapter-one.events` (one for everything) |
| **queue** | one service's mailbox | `inventory.order-created`, `order.stock-reserved`, ... |
| **bind** | "put copies of *this kind* of note in my mailbox" | a queue asks for an event type |
| **publish** | dropping a note at the sorting desk | `publisher.publish(event)` |
| **consume** | taking notes out of your mailbox | each service's `app/consumer.py` |
| **ack** | "done with this note, you can throw it away" | `message.ack()` |

Two things follow from this design that are worth noticing:

- **The sender doesn't know who's listening.** Order announces "order confirmed"; it
  doesn't know that cart empties the cart when it hears that. When notification joins
  in step 5, order won't change at all.
- **A series of steps across services, held together by messages, is called a saga.**
  There's no single transaction covering order's and inventory's databases (step 3,
  Part 1). Instead each service makes its own change and then passes the baton.

```
   You
    │
 Gateway :8000
    │              │                         │
  Catalog :8001   Cart :8002 ── Redis      Order :8004 ── Postgres (order_db)
                   ▲                         │  ▲
                   │ OrderConfirmed          │  │ StockReserved / StockFailed
                   │                         ▼  │
              ┌────┴──────────────── RabbitMQ :5672 ───────────────┐
              │   exchange "chapter-one.events" + one queue each   │
              └──────────────────────────────┬─────────────────────┘
                                             │ OrderCreated
                                             ▼
                                      Inventory :8003 ── Postgres (inventory_db)
```

Inventory no longer has any HTTP route for reserving. Messages are the only way in.

---

## Part 2: What step 4 built

```bash
docker compose up --build -d

ME=$(python3 -c 'import uuid; print(uuid.uuid4())')
DRACULA=11111111-1111-4111-8111-000000000005
TOKEN=$(docker compose exec -T gateway python -m app.dev_token $ME)
AUTH="Authorization: Bearer $TOKEN"

curl -X PUT localhost:8000/api/carts/$ME/items/$DRACULA -H "$AUTH" \
  -H 'content-type: application/json' -d '{"quantity": 1}'
curl -i -X POST localhost:8000/api/orders -H "$AUTH"   # 202, status PENDING
curl localhost:8000/api/orders -H "$AUTH"              # a moment later: CONFIRMED
curl localhost:8000/api/carts/$ME -H "$AUTH"           # empty again

# The RabbitMQ dashboard: queues, message counts, publish a message by hand
# http://localhost:15672   (user and password: chapter-one)
```

### Following one checkout from start to finish

1. **You → gateway → order:** `POST /api/orders`. Order reads your cart, copies titles and
   prices (as in step 3), and saves the order as **PENDING**.
2. **Order publishes `OrderCreated`** ("order #123: 1 × Dracula") and answers you
   **202 Accepted**: "got it, not finished yet". This took about 0.03 seconds in our
   tests, faster than step 3, because nobody waits for inventory.
3. **Inventory** finds the note in its mailbox `inventory.order-created`, reserves the
   copies with the same all-or-nothing, never-oversell logic as step 3, and publishes
   **`StockReserved`**, or **`StockFailed`** with a reason like `INSUFFICIENT_STOCK`.
4. **Order** finds the answer in `order.stock-reserved` (or `order.stock-failed`), changes
   the order to **CONFIRMED** (or **CANCELLED** with the reason), and publishes
   **`OrderConfirmed`** (or **`OrderCancelled`**).
5. **Cart** finds `OrderConfirmed` in `cart.order-confirmed` and empties your cart. A
   cancelled order leaves your cart alone, so you can try again.

Steps 3 to 5 together took about 60 milliseconds. You find out the result by looking at
the order again (`GET /api/orders/{id}`). A real frontend would check every second or so.

Every note carries the same **correlation ID** from step 1 to step 5, so you can follow
one checkout through all the services' logs (hard rule 7).

### 1. The envelope: every note looks the same on the outside

`shared/events.py` defines the **envelope** all events share (see CLAUDE.md): an
`eventId`, the `eventType`, when it happened, the `correlationId`, a `version`, and
the `payload` with the actual content. It also defines each payload's shape
(`OrderCreatedPayload`, `StockFailedPayload`, ...).

This lives in `shared/` because it's a **contract** between services, like the API
paths. What a service *does* with an event stays in that service. CLAUDE.md is strict
about this: shared business logic would tie the services together.

Payloads quietly ignore fields they don't know. So a sender can *add* a field later
without breaking older listeners. Removing or renaming a field is a breaking change and
needs a new `version`.

### 2. Sending a note so it doesn't get lost on the way

`services/order/app/events.py` publishes with three safety settings:

- **Publisher confirms:** order waits until RabbitMQ says "got it" before answering you.
- **Durable exchange, durable queues, persistent messages:** RabbitMQ writes them to
  disk, so a RabbitMQ restart doesn't lose them.
- **`mandatory`:** if no mailbox wants a note, RabbitMQ hands it back and we log
  "event not delivered to any queue" instead of it vanishing silently. You'll still see
  this for `OrderCancelled`, because nobody listens for it until notification (step 5).

### 3. Reading notes: the same shape in three services

Inventory, order and cart each have an `app/consumer.py` that works the same way:

1. **Connect in the background** and keep retrying every 3s. The service starts even
   while RabbitMQ is down, and `/health/ready` says "consumer unreachable" until it
   connects.
2. **For each note:** read it, do the work, publish any follow-up note, and **ack last**.
   If the service crashes before the ack, RabbitMQ hands the note out again. Nothing is
   lost, but a note can arrive twice (Part 3).
3. **Unreadable note** (not JSON, wrong shape, unknown order): retrying won't help, so
   it's thrown away and logged as an error.
4. **Temporary trouble** (database or Redis down): wait 2 seconds, then put the note
   back in the mailbox to try again.
5. **On shutdown:** stop taking new notes, give the ones in progress up to 5 seconds,
   then disconnect.

### 4. Who decided what

- **Order answers 202, not 201.** 201 means "created, done". 202 means "accepted, still
  working on it", which is honest while the order is PENDING.
- **One mailbox per event type** (`order.stock-reserved` and `order.stock-failed`), not
  one per service. That's CLAUDE.md's naming rule, and the dashboard then shows exactly
  which kind of note is piling up.
- **Inventory's HTTP reservation endpoint was removed** (piece 6). With it, books could
  be reserved for an order that never existed. Now every reservation comes from a real
  `OrderCreated`.
- **The money in `OrderConfirmed`** is `totalMinor` + `currency` (whole cents), like
  everywhere else.

---

## Part 3: Every message may arrive twice

RabbitMQ promises **at least once**, not **exactly once**. The reason is the ack: if a
service did the work but crashed before saying "done", RabbitMQ can't know, so it
delivers the note again. Hard rule 3 says every listener must cope with that.

Each of our three listeners copes in a different way, because each has different data:

| Listener | What a repeat would do if we were careless | How it's prevented |
|----------|---------------------------------------------|--------------------|
| **Inventory** | take the copies twice | The order id is the key of the `reservations` table. A repeat finds the stored outcome (RESERVED *or* FAILED) and sends the same answer again without touching stock. |
| **Order** | flip a finished order again | `UPDATE ... WHERE status = 'PENDING'`: only a pending order can change. A repeat finds it already CONFIRMED and changes nothing. |
| **Cart** | empty a *new* cart the user filled since | A note in Redis, `cart-emptied-for:<orderId>`, set **in the same step** as the delete (a tiny Lua script, which Redis runs as one step). If the note is already there, nothing happens. |

Notice that inventory and order **send their answer again** on a repeat. That looks odd
until you think about this crash: "did the work, crashed before the answer went out".
The repeat is the only chance to send it. So a repeated input can produce a repeated
output, and the next listener must cope with that too. That's why cart keys its note by
**order id**: two `OrderConfirmed` notes for the same order have different event ids, but
they mean the same thing.

The tests for this are called things like
`test_same_answer_twice_publishes_the_follow_up_again` and
`test_repeat_after_a_new_cart_leaves_the_new_cart_alone`.

---

## Part 4: Breaking it on purpose (again)

Same method as step 3 (`docker compose stop` = switched off, `pause` = frozen), run on
2026-11-11. "Answer" is what the customer's `POST /api/orders` got back.

| What broke | Answer | Step 3 answered | What happened afterwards |
|------------|--------|-----------------|--------------------------|
| Nothing | 202 PENDING, 0.04s | 201 CONFIRMED, 0.1s | CONFIRMED within 0.3s, cart emptied |
| Catalog off | 202 PENDING | 201 CONFIRMED | CONFIRMED |
| Cart off | 503 `CART_UNAVAILABLE`, 0.6s | same | nothing stored (order can't know what to order) |
| Inventory off | **202 PENDING** | 503 | PENDING while it's off; **CONFIRMED ~5s after it started again** |
| Inventory's database off | **202 PENDING** | 503 | inventory retried every 2s; **CONFIRMED once the DB was back** |
| Inventory **frozen**, then unfrozen | **202 PENDING** | 503, order stuck PENDING, copy taken ⚠ | **CONFIRMED** after unfreezing, 1 copy taken, cart emptied ✅ |
| Order off while inventory answered | (order was up when placing) | — | answer waited in `order.stock-reserved` (1 message); **CONFIRMED** when order came back |
| Cart off when `OrderConfirmed` was sent | (cart was up when placing) | — | order CONFIRMED anyway; note waited in `cart.order-confirmed`; **cart emptied** when cart came back |
| **RabbitMQ off** | 503 `BROKER_UNAVAILABLE`, 0.02s | — | order **stuck PENDING**, cart not emptied ⚠ |
| Rush: 6 customers, 2 Draculas | 6 × 202 | 6 answers | **2 CONFIRMED, 4 CANCELLED, stock 0** |
| Order twice, one after the other | 202, then 409 `CART_EMPTY` | 2 orders | one order |
| Double click (two at the same moment) | 202, 202 | 2 orders | **two orders** ⚠ |

### The lost answer is gone

Step 3's worst row was the frozen inventory: the customer was told "failed", the copy
was taken anyway, and the order stayed PENDING forever with nobody noticing. Now:

1. Order saved the order, dropped `OrderCreated` in the post room and answered 202.
2. Inventory was frozen. The note sat in `inventory.order-created`.
3. Inventory was unfrozen, found the note, reserved the copy and sent `StockReserved`.
4. Order heard it and confirmed the order. Cart heard *that* and emptied the cart.

Nobody gave up halfway, so nothing could be lost. A slow or broken stockroom now means
a **late** answer, never a **missing** one. The same goes for any service being down:
its mailbox fills up and it catches up when it's back (the "order off" and "cart off"
rows).

### What still breaks

- **RabbitMQ itself down.** Order saves the order, then can't drop off the note. It
  tells you 503, but the order is saved as PENDING and nothing will ever send its note
  later. This is the "save, *then* publish" gap: two separate steps, with room to fail
  in between. **Step 5's outbox** fixes it (Part 5).
- **Double click.** Two `POST /orders` at the same moment both read the full cart
  before either order is confirmed, so you get two orders. Ordering twice *in a row* is
  fine now, because the cart was emptied in between.
- **Cart off when ordering** still fails, as it should: the cart *is* the order's
  content.

---

## Part 5: Known weak spots, and what step 5 does about them

**1. Save, then publish (the big one).** If RabbitMQ is down, or order crashes between
saving the order and publishing `OrderCreated`, that order is stuck PENDING. **Step 5's
outbox:** order writes the note into an `outbox` table *in the same database
transaction* as the order. Either both are saved or neither is. A separate worker then
reads the outbox and publishes, retrying until RabbitMQ takes it (hard rule 4).
Inventory and order have the same gap for their reply notes. A repeated input heals it
there, but the outbox is the proper fix.

**2. Nobody listens for `OrderCancelled` yet.** **Step 5's notification service** will
consume `OrderConfirmed` and `OrderCancelled` and log a "fake email". It has to cope with
repeats too, and since a repeat can come with a new event id, it should go by order id.

**3. Unreadable notes are thrown away.** They're logged, but the note itself is gone.
A **dead-letter queue** (a mailbox for rejected notes) would keep them for a human to
look at. Worth considering in step 5.

**4. The whole cart is emptied,** including a book added in the moment between ordering
and confirmation. That moment is usually tiny, but it's longer when inventory is down.
The fix would be to list the ordered books in `OrderConfirmed` and remove only those.

**5. Double click makes two orders.** An `Idempotency-Key` header (the client sends the
same random key with both clicks, and order only accepts it once) would fix this.

**6. Copies are never given back.** Fine today, because a cancelled order never reserved
anything. It will matter once orders can be cancelled *after* reserving.

**7. The mailbox-reading code is copied three times** (inventory, order, cart), about
80 lines each. Moving it into `shared/messaging.py` before notification makes it four is
a good small tidy-up.

**8. You have to ask again to see the result.** The frontend will check the order every
second or so until it's no longer PENDING. Pushing the result to the browser (e.g.
WebSockets) is possible but not on the roadmap.

---

## Part 6: Where to find things

```
shared/
├── events.py            the envelope + every payload shape + event type names  ← start here
└── messaging.py         the exchange settings, wrapping an event as a message

services/order/app/
├── orders.py            POST /orders (save PENDING, publish, 202); finish_order()
├── events.py            EventPublisher: confirms, persistent, mandatory, 1.5s budget
└── consumer.py          StockReserved/StockFailed in → CONFIRMED/CANCELLED → follow-up out

services/inventory/app/
├── consumer.py          OrderCreated in → reserve → StockReserved/StockFailed out
└── reservations.py      the reserving itself (unchanged rules from step 3; no HTTP route)

services/cart/app/
├── consumer.py          OrderConfirmed in → empty the cart
└── carts.py             empty_cart_for_order(): the Lua script, once per order

docker-compose.yml       the rabbitmq service; additional_contexts hands shared/ to builds
```

Look inside RabbitMQ: http://localhost:15672 → **Queues** shows each mailbox and how
many notes are waiting. **Exchanges → chapter-one.events → Publish message** lets you
drop a note by hand (routing key = event type, e.g. `OrderCreated`).

Follow one checkout through all the logs:

```bash
docker compose logs order inventory cart | grep <correlationId>
```

---

## Part 7: Check yourself

Try answering before you open the answers.

1. Inventory is switched off for ten minutes. What does a customer who orders during
   that time see, and what happens when inventory comes back?
2. Why does order answer 202 instead of 201?
3. What's the difference between an exchange and a queue?
4. Why does a consumer ack a note at the **end**, not as soon as it receives it?
5. Inventory gets the same `OrderCreated` twice. Why doesn't it take the copies twice,
   and why does it send its answer again?
6. Why can't cart simply delete the cart every time it hears `OrderConfirmed`?
7. Order doesn't know that cart listens for `OrderConfirmed`. Why is that a good thing?
8. RabbitMQ is down when you order. What goes wrong, and what will step 5 change?
9. Why is the envelope in `shared/`, but the code that reserves stock isn't?

<details>
<summary>Answers</summary>

1. They get 202 and a PENDING order straight away. The `OrderCreated` note waits in
   inventory's mailbox. When inventory starts, it handles the note, and a few seconds
   later the order is CONFIRMED (or CANCELLED) and the cart is emptied. In step 3 the
   customer got a 503 instead.
2. The order isn't finished when order answers: inventory hasn't said anything yet. 202
   means "accepted, still working on it". 201 would claim it's done.
3. The exchange is the sorting desk every note is dropped at. A queue is one service's
   mailbox. Queues tell the exchange which event types they want (binding), and the
   exchange puts a copy of each note into every mailbox that asked for it.
4. If the service crashes halfway, the note wasn't acked, so RabbitMQ delivers it again
   and the work gets done. Acking first would mean a crash loses the note for good.
5. The reservation is stored under the order id, together with its outcome. A repeat
   finds it and changes nothing. It answers again because the repeat might be happening
   *because* the first answer never went out (crash after reserving, before answering).
6. A repeat of the same `OrderConfirmed` could arrive after the user started a new
   cart, and would delete those new books. Cart remembers "already emptied for this
   order" and only empties once per order.
7. New listeners (notification in step 5) can be added without touching order. Order
   also doesn't depend on cart being up: if cart is down, the note waits in its mailbox.
8. Order has already saved the order as PENDING but can't drop off the note, so
   inventory never hears about it and the order is stuck forever. The outbox saves the
   note in the same database transaction as the order, and a worker keeps trying to
   publish it until RabbitMQ is back.
9. The envelope and payload shapes are a contract every sender and listener must agree
   on, like an API. Reserving stock is inventory's own business. If it were shared,
   other services would depend on inventory's internals, and changing it would mean
   changing everyone (CLAUDE.md: no business logic in `shared/`).

</details>
