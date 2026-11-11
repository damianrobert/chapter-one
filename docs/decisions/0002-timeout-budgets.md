# 0002 — Time budgets between services

- Status: accepted
- Date: 2026-11-11

## Context

Hard rule 6 says every outbound HTTP call has a timeout (default 2s) and a limited
retry. Until now each service applied "2s" to **each attempt**, with up to 3 attempts
plus pauses. So one call could really take about 6.6s.

That broke as soon as calls were chained:

- The gateway waited 2s for cart, but cart could spend 6.6s waiting for catalog.
  On 2026-11-10 we froze catalog: the client got a 503 after 2s, while cart carried
  on and saved the item anyway once catalog came back.
- `POST /orders` calls cart and then inventory. That's up to about 13s of waiting,
  behind a gateway that gave up after 2s. The customer would see "order service
  unavailable" while the order was still being placed.

When the caller gives up before the service behind it is finished, the work still
happens, but nobody gets told the result.

## Decision

1. **A budget covers the whole call**, including retries and pauses. Each client
   wraps its retry loop in `asyncio.timeout(budget)`. Quick failures (connection
   refused) are still retried while budget is left. A service that hangs uses up the
   budget once and isn't waited for again.
2. **A caller waits longer than everything the service behind it may do.** Budgets
   shrink as a request goes deeper into the system:

| Caller → service       | Budget | Why this number                                      |
|------------------------|--------|------------------------------------------------------|
| cart → catalog         | 1.5s   | Leaf call                                            |
| order → cart           | 1.5s   | Leaf call (reading a cart doesn't call catalog)      |
| order → inventory      | 1.5s   | Leaf call (step 3 only; removed in step 4)           |
| order → RabbitMQ       | 1.5s   | Publish + broker's confirm (step 4 on)               |
| gateway → catalog      | 2s     | Hard rule 6's default                                |
| gateway → cart         | 2s     | More than cart's 1.5s on catalog                     |
| gateway → order        | **5s** | Order makes two calls (max 3s) plus DB writes. From step 4 these are cart + publishing, still max 3s |

The budgets are constants next to the code that uses them:
`services/gateway/app/routes.py` (`BUDGETS`), `services/cart/app/catalog_client.py` and
`services/order/app/clients.py` and `services/order/app/events.py` (`BUDGET_SECONDS`). Tests check that the numbers
still nest correctly.

## Consequences

- Clients get an answer, often a clear 503 from the service closest to the problem,
  instead of the gateway's generic "service unavailable".
- Fewer retries in practice: a hanging service eats the budget in one attempt.
- Order gets 5s, more than the 2s default. That's acceptable for a checkout, but it
  shows the cost of a synchronous chain: each extra hop makes the customer wait
  longer. Roadmap step 4 removes the inventory call from this chain (events instead).
- The numbers are kept in sync by hand, across services. If a budget changes, check
  the table above. Something like deadline propagation (passing "you have 1.2s left"
  along with each request) would do this automatically, but it's more machinery than
  this project needs right now.
- Database and Redis timeouts (2s connect) aren't counted inside these budgets. They
  are local and fast in practice. Revisit if that changes.
