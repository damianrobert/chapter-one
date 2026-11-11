"""The order API: place an order from your cart, list your orders, look one up.

## Placing an order (step 3: synchronous on purpose)

`POST /orders` does everything while the customer waits:

1. Read the user's cart from the cart service.
2. Save the order as PENDING, with title, author and price **copied** from the cart
   (hard rule 2). From now on the order never looks at cart or catalog again.
3. Ask inventory to reserve the books, and wait for the answer.
4. Inventory said yes  -> CONFIRMED.
   Inventory said no   -> CANCELLED, reason INSUFFICIENT_STOCK.
   Inventory is down   -> the order stays PENDING and the client gets 503.

Why save as PENDING *before* calling inventory? Inventory reserves stock under the
order's id, so the order must have an id first. And if something fails halfway, the
order is still written down instead of silently lost.

Why leave it PENDING when inventory doesn't answer, instead of cancelling? We don't
know what happened: inventory may have reserved the books and only the answer got
lost. Cancelling would then keep books locked away for an order that "failed".
PENDING honestly says "we don't know yet". This is exactly the weakness that step 4
fixes: with events, the answer arrives later instead of being lost.

Both CONFIRMED and CANCELLED orders answer 201: in both cases a new order was
created; its `status` says how it went.

The cart is NOT emptied after a confirmed order yet. Per CLAUDE.md that happens when
cart receives the OrderConfirmed event (roadmap step 4/5).

## Who may see which order

The gateway checks the login and forwards the user's id in `X-User-ID` (hard rule 8).
Order trusts that header: you only ever see your own orders. Someone else's order id
answers 404, the same as an id that doesn't exist, so ids can't be probed.
"""

import logging
from typing import Annotated
from uuid import UUID

import httpx2
from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app import clients
from app.config import settings
from app.db import Engine
from app.errors import ApiError
from app.schemas import Order, OrderItem, OrderPage
from app.tables import CANCELLED, CONFIRMED, PENDING, order_items, orders

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orders", tags=["orders"])


def get_http_client(request: Request) -> httpx2.AsyncClient:
    return request.app.state.http_client


HttpClient = Annotated[httpx2.AsyncClient, Depends(get_http_client)]
# The logged-in user, set by the gateway. Missing or not a UUID -> 400 VALIDATION_ERROR
# (that only happens if someone calls order directly, bypassing the gateway).
UserId = Annotated[UUID, Header(alias="X-User-ID")]


# --- placing an order ---


@router.post("", status_code=201, response_model=Order)
async def place_order(user_id: UserId, engine: Engine, http: HttpClient) -> Order:
    try:
        cart = await clients.get_cart(http, settings.cart_url, user_id)
    except clients.ServiceUnavailable as exc:
        raise ApiError(503, "CART_UNAVAILABLE", "The cart service is unavailable") from exc

    if not cart["items"]:
        raise ApiError(409, "CART_EMPTY", "The cart is empty; add books before ordering")

    order_id = await save_pending_order(engine, user_id, cart)
    logger.info("order created", extra={"orderId": str(order_id), "status": PENDING})

    reservation_items = [
        {"bookId": item["bookId"], "quantity": item["quantity"]} for item in cart["items"]
    ]
    try:
        await clients.reserve_stock(http, settings.inventory_url, order_id, reservation_items)
    except clients.InsufficientStock:
        await finish_order(engine, order_id, CANCELLED, cancel_reason="INSUFFICIENT_STOCK")
    except clients.ServiceUnavailable as exc:
        logger.error("order left pending: inventory unavailable", extra={"orderId": str(order_id)})
        raise ApiError(
            503,
            "INVENTORY_UNAVAILABLE",
            "The inventory service is unavailable; the order is still pending",
            details=[{"orderId": str(order_id)}],
        ) from exc
    else:
        await finish_order(engine, order_id, CONFIRMED)

    return await load_order(engine, order_id, user_id)


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


async def finish_order(
    engine: AsyncEngine, order_id: UUID, status: str, cancel_reason: str | None = None
) -> None:
    """Move a PENDING order to CONFIRMED or CANCELLED.

    `WHERE status = 'PENDING'` enforces the only allowed transitions (CLAUDE.md): a
    confirmed or cancelled order never changes again.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            update(orders)
            .where(orders.c.id == order_id, orders.c.status == PENDING)
            .values(status=status, cancel_reason=cancel_reason, updated_at=func.now())
        )
    if result.rowcount != 1:
        # Can't happen in step 3 (only this request touches the new order), but if it
        # ever does we want to hear about it rather than silently overwrite.
        logger.error(
            "order was not pending, status not changed",
            extra={"orderId": str(order_id), "wantedStatus": status},
        )
    else:
        logger.info("order finished", extra={"orderId": str(order_id), "status": status})


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
