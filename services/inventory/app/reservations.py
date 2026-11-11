"""Reserving stock for an order: all books or none, and safe to repeat.

The order service calls `PUT /reservations/{orderId}` with the books it wants. Three
things make this correct even with many orders arriving at the same moment:

1. **Atomic, conditional updates (hard rule 5).** For each book we run

       UPDATE stock SET quantity = quantity - :n
       WHERE book_id = :id AND quantity >= :n

   and check that it changed one row. The database checks and subtracts in one step,
   locking the row while it does. A "read the quantity, check it in Python, then
   write" approach would let two orders both read "2 left", both pass the check, and
   sell 4 copies of a book we have 2 of.

2. **One transaction for the whole order.** If the third book is out of stock, the
   updates for the first two are rolled back too. Nobody ever sees half a reservation.

3. **Repeating is harmless (idempotent).** The order id is the reservation's primary
   key. If the same order asks again — e.g. it retried because the first answer got
   lost on the network — we find the existing reservation and return it instead of
   taking the copies twice. That's also why this is a PUT with the id in the path:
   in HTTP, PUT means "make it so", which is safe to repeat.

There's no "release" yet: in step 3 an order that fails reserves nothing. Giving
copies back (e.g. for a cancelled order) is a later concern.
"""

from uuid import UUID

from fastapi import APIRouter, Response
from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db import Engine
from app.errors import ApiError
from app.schemas import Reservation, ReservationItem, ReservationRequest
from app.tables import reservation_items, reservations, stock

router = APIRouter(prefix="/reservations", tags=["reservations"])


async def reserve_stock(
    engine: AsyncEngine, order_id: UUID, items: list[ReservationItem]
) -> tuple[Reservation, bool]:
    """Reserve `items` for `order_id`. Returns (reservation, created).

    `created` is False when this order already had the same reservation. Raises
    ApiError 409 when stock is short or the order already reserved different items.
    """
    # engine.begin(): one transaction. Raising anywhere inside rolls back everything,
    # including stock already subtracted for earlier books in the list.
    async with engine.begin() as conn:
        # Claim the order id first. ON CONFLICT DO NOTHING + RETURNING gives back the
        # id if we inserted it, or nothing if a reservation for this order exists.
        # If another request for the same order is running right now, Postgres makes
        # this wait until that one commits or rolls back, so we never both proceed.
        claimed = await conn.scalar(
            pg_insert(reservations)
            .values(order_id=order_id)
            .on_conflict_do_nothing()
            .returning(reservations.c.order_id)
        )
        if claimed is None:
            existing = await _load_items(conn, order_id)
            if _same_items(existing, items):
                return Reservation(order_id=order_id, items=existing), False
            raise ApiError(
                409,
                "RESERVATION_CONFLICT",
                f"Order {order_id} already has a reservation with different items",
            )

        # Update books in a fixed order (sorted by id). Two orders that both want
        # books A and B then lock A first, then B. Without this, one could lock A
        # while the other locks B, and each would wait for the other forever (a
        # "deadlock"; Postgres would detect it and fail one of them).
        for item in sorted(items, key=lambda i: i.book_id):
            result = await conn.execute(
                update(stock)
                .where(stock.c.book_id == item.book_id, stock.c.quantity >= item.quantity)
                .values(quantity=stock.c.quantity - item.quantity, updated_at=func.now())
            )
            # 0 rows changed: not enough copies, or no stock record for this book at
            # all. Either way the answer to the order is the same: can't have it.
            if result.rowcount != 1:
                raise ApiError(
                    409,
                    "INSUFFICIENT_STOCK",
                    f"Not enough stock for book {item.book_id}",
                    details=[{"bookId": str(item.book_id), "requested": item.quantity}],
                )

        await conn.execute(
            insert(reservation_items),
            [{"order_id": order_id, "book_id": i.book_id, "quantity": i.quantity} for i in items],
        )

    return Reservation(order_id=order_id, items=items), True


async def _load_items(conn: AsyncConnection, order_id: UUID) -> list[ReservationItem]:
    rows = (
        await conn.execute(
            select(reservation_items.c.book_id, reservation_items.c.quantity)
            .where(reservation_items.c.order_id == order_id)
            .order_by(reservation_items.c.book_id)
        )
    ).mappings()
    return [ReservationItem.model_validate(dict(row)) for row in rows]


def _same_items(a: list[ReservationItem], b: list[ReservationItem]) -> bool:
    def as_dict(items: list[ReservationItem]) -> dict[UUID, int]:
        return {i.book_id: i.quantity for i in items}

    return as_dict(a) == as_dict(b)


@router.put("/{order_id}", response_model=Reservation)
async def put_reservation(
    order_id: UUID, body: ReservationRequest, response: Response, engine: Engine
) -> Reservation:
    reservation, created = await reserve_stock(engine, order_id, body.items)
    # 201 the first time, 200 when it already existed — the body is the same either way.
    response.status_code = 201 if created else 200
    return reservation
