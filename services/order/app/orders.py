"""The order API: place an order from your cart, list your orders, look one up.

## Placing an order (step 4: with events)

`POST /orders` no longer waits for inventory. It does only what order itself can do,
then answers right away:

1. Read the user's cart from the cart service.
2. Save the order as PENDING, with title, author and price **copied** from the cart
   (hard rule 2). From now on the order never looks at cart or catalog again.
3. Publish an `OrderCreated` event to RabbitMQ ("this order needs these books").
4. Answer **202 Accepted** with the PENDING order: "got it, we're working on it".

Later, and without the customer waiting, inventory picks up the event, reserves the
books and replies with its own event; order then moves the order to CONFIRMED or
CANCELLED (`finish_order()` below, called from app/consumer.py). The client finds out
by looking at the order again (`GET /orders/{id}`). That's the **checkout saga** in
CLAUDE.md.

Why 202 and not 201? 201 means "created, here's the result". 202 means "accepted,
not finished yet", which is the honest answer while the order is still PENDING.

If RabbitMQ doesn't take the event, the order stays PENDING and the client gets 503
`BROKER_UNAVAILABLE` with the order id. Nothing re-sends the event later: that order is
stuck. Step 5's outbox pattern closes this gap (hard rule 4).

## Who may see which order

The gateway checks the login and forwards the user's id in `X-User-ID` (hard rule 8).
Order trusts that header: you only ever see your own orders. Someone else's order id
answers 404, the same as an id that doesn't exist, so ids can't be probed.
"""

import logging
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID, uuid4

import httpx2
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app import clients
from app.config import settings
from app.db import Engine
from app.errors import ApiError
from app.events import BrokerUnavailable, EventPublisher
from app.schemas import Order, OrderItem, OrderPage
from app.tables import PENDING, order_items, orders
from shared.events import ORDER_CREATED, OrderCreatedItem, OrderCreatedPayload, new_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orders", tags=["orders"])


def get_http_client(request: Request) -> httpx2.AsyncClient:
    return request.app.state.http_client


def get_publisher(request: Request) -> EventPublisher:
    return request.app.state.publisher


HttpClient = Annotated[httpx2.AsyncClient, Depends(get_http_client)]
Publisher = Annotated[EventPublisher, Depends(get_publisher)]
# The logged-in user, set by the gateway. Missing or not a UUID -> 400 VALIDATION_ERROR
# (that only happens if someone calls order directly, bypassing the gateway).
UserId = Annotated[UUID, Header(alias="X-User-ID")]
CorrelationHeader = Annotated[str | None, Header(alias="X-Correlation-ID")]


def correlation_id_from(header: str | None) -> UUID:
    """The request's correlation ID, which every event it causes must carry (hard rule 7).

    Roadmap step 6 makes the gateway always send one. Until then, use the header if
    it's there and a valid UUID, otherwise start a new one here.
    """
    try:
        return UUID(header)
    except (TypeError, ValueError):
        return uuid4()


# --- placing an order ---


@router.post("", status_code=202, response_model=Order)
async def place_order(
    user_id: UserId,
    engine: Engine,
    http: HttpClient,
    publisher: Publisher,
    response: Response,
    correlation_header: CorrelationHeader = None,
) -> Order:
    correlation_id = correlation_id_from(correlation_header)
    try:
        cart = await clients.get_cart(http, settings.cart_url, user_id)
    except clients.ServiceUnavailable as exc:
        raise ApiError(503, "CART_UNAVAILABLE", "The cart service is unavailable") from exc

    if not cart["items"]:
        raise ApiError(409, "CART_EMPTY", "The cart is empty; add books before ordering")

    order_id = await save_pending_order(engine, user_id, cart)
    logger.info(
        "order created",
        extra={"orderId": str(order_id), "status": PENDING, "correlationId": str(correlation_id)},
    )

    # Publish only after the order is committed: whoever reacts to the event may look
    # for the order (or send a reply about it), so it must already exist.
    event = new_event(
        ORDER_CREATED,
        order_created_payload(order_id, user_id, cart),
        correlation_id,
    )
    try:
        await publisher.publish(event)
    except BrokerUnavailable as exc:
        logger.error("order left pending: event not published", extra={"orderId": str(order_id)})
        raise ApiError(
            503,
            "BROKER_UNAVAILABLE",
            "The order was saved but could not be passed on for processing; it stays pending",
            details=[{"orderId": str(order_id)}],
        ) from exc

    # Where the client can check on the order (it's still PENDING).
    response.headers["Location"] = f"/orders/{order_id}"
    return await load_order(engine, order_id, user_id)


def order_created_payload(order_id: UUID, user_id: UUID, cart: dict) -> dict:
    """The OrderCreated payload (CLAUDE.md events table): orderId, userId, items."""
    payload = OrderCreatedPayload(
        order_id=order_id,
        user_id=user_id,
        items=[
            OrderCreatedItem(book_id=item["bookId"], qty=item["quantity"]) for item in cart["items"]
        ],
    )
    return payload.to_dict()


async def save_pending_order(engine: AsyncEngine, user_id: UUID, cart: dict) -> UUID:
    """Write the order and its items in one transaction; return the new order id."""
    # Work the total out ourselves from the copied lines rather than trusting the
    # cart's own total: the stored total must match the stored items.
    total = sum(item["priceMinor"] * item["quantity"] for item in cart["items"])

    async with engine.begin() as conn:
        order_id = await conn.scalar(
            insert(orders)
            .values(user_id=user_id, status=PENDING, total_minor=total, currency=cart["currency"])
            .returning(orders.c.id)
        )
        await conn.execute(
            insert(order_items),
            [
                {
                    "order_id": order_id,
                    "book_id": item["bookId"],
                    "title": item["title"],
                    "author": item["author"],
                    "unit_price_minor": item["priceMinor"],
                    "quantity": item["quantity"],
                }
                for item in cart["items"]
            ],
        )
    return order_id


@dataclass(frozen=True)
class FinishedOrder:
    """An order after finish_order(): what its follow-up event needs."""

    user_id: UUID
    status: str  # CONFIRMED or CANCELLED (could differ from what was asked, see below)
    cancel_reason: str | None
    total_minor: int
    currency: str
    changed: bool  # False: it was already finished before this call


async def finish_order(
    engine: AsyncEngine, order_id: UUID, status: str, cancel_reason: str | None = None
) -> FinishedOrder | None:
    """Move a PENDING order to CONFIRMED or CANCELLED. None if the order doesn't exist.

    Called when inventory's reply event (StockReserved / StockFailed) arrives
    (app/consumer.py).

    `WHERE status = 'PENDING'` enforces the only allowed transitions (CLAUDE.md): a
    confirmed or cancelled order never changes again. If the order was already
    finished (the same reply delivered twice), nothing changes and we return the order
    as it is, with `changed=False`. The caller compares `status` with what it asked for.
    """
    wanted = [
        orders.c.user_id,
        orders.c.status,
        orders.c.cancel_reason,
        orders.c.total_minor,
        orders.c.currency,
    ]
    async with engine.begin() as conn:
        # RETURNING hands back the updated row, so no second query is needed.
        row = (
            (
                await conn.execute(
                    update(orders)
                    .where(orders.c.id == order_id, orders.c.status == PENDING)
                    .values(status=status, cancel_reason=cancel_reason, updated_at=func.now())
                    .returning(*wanted)
                )
            )
            .mappings()
            .first()
        )
        changed = row is not None
        if not changed:
            # Not pending (or not there at all): look at it as it is.
            row = (
                (await conn.execute(select(*wanted).where(orders.c.id == order_id)))
                .mappings()
                .first()
            )
    if row is None:
        return None
    if changed:
        logger.info("order finished", extra={"orderId": str(order_id), "status": status})
    return FinishedOrder(**row, changed=changed)


# --- reading orders ---


@router.get("", response_model=OrderPage)
async def list_orders(
    user_id: UserId,
    engine: Engine,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OrderPage:
    """The user's orders, newest first (uses the (user_id, created_at) index)."""
    async with engine.connect() as conn:
        total = await conn.scalar(
            select(func.count()).select_from(orders).where(orders.c.user_id == user_id)
        )
        rows = (
            await conn.execute(
                select(orders)
                .where(orders.c.user_id == user_id)
                .order_by(orders.c.created_at.desc(), orders.c.id)
                .limit(limit)
                .offset(offset)
            )
        ).mappings()
        order_rows = list(rows)
        items_by_order = await _load_items(conn, [row["id"] for row in order_rows])

    return OrderPage(
        items=[_to_order(row, items_by_order.get(row["id"], [])) for row in order_rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{order_id}", response_model=Order)
async def get_order(order_id: UUID, user_id: UserId, engine: Engine) -> Order:
    return await load_order(engine, order_id, user_id)


async def load_order(engine: AsyncEngine, order_id: UUID, user_id: UUID) -> Order:
    async with engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    select(orders).where(orders.c.id == order_id, orders.c.user_id == user_id)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError(404, "ORDER_NOT_FOUND", f"Order {order_id} not found")
        items_by_order = await _load_items(conn, [order_id])
    return _to_order(row, items_by_order.get(order_id, []))


async def _load_items(conn: AsyncConnection, order_ids: list[UUID]) -> dict[UUID, list[OrderItem]]:
    """Items for several orders in ONE query (not one query per order)."""
    if not order_ids:
        return {}
    rows = (
        await conn.execute(
            select(order_items)
            .where(order_items.c.order_id.in_(order_ids))
            .order_by(order_items.c.title, order_items.c.book_id)
        )
    ).mappings()
    result: dict[UUID, list[OrderItem]] = {}
    for row in rows:
        result.setdefault(row["order_id"], []).append(
            OrderItem(
                book_id=row["book_id"],
                title=row["title"],
                author=row["author"],
                unit_price_minor=row["unit_price_minor"],
                quantity=row["quantity"],
                line_total_minor=row["unit_price_minor"] * row["quantity"],
            )
        )
    return result


def _to_order(row, items: list[OrderItem]) -> Order:
    return Order(
        id=row["id"],
        user_id=row["user_id"],
        status=row["status"],
        cancel_reason=row["cancel_reason"],
        items=items,
        total_minor=row["total_minor"],
        currency=row["currency"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
