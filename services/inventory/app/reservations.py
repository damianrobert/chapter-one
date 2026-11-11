"""Reserving stock for an order: all books or none, and safe to repeat.

Inventory reserves stock when it receives an `OrderCreated` event (app/consumer.py).
That's the only way in: step 3's `PUT /reservations/{orderId}` was removed in step 4,
so copies are only ever reserved because an order was placed. Three things make this
correct even with many orders arriving at the same moment:

1. **Atomic, conditional updates (hard rule 5).** For each book we run

       UPDATE stock SET quantity = quantity - :n
       WHERE book_id = :id AND quantity >= :n

   and check that it changed one row. The database checks and subtracts in one step,
   locking the row while it does. A "read the quantity, check it in Python, then
   write" approach would let two orders both read "2 left", both pass the check, and
   sell 4 copies of a book we have 2 of.

2. **All or nothing.** If the third book is out of stock, the updates for the first
   two are rolled back too (a *savepoint*: a "rewind to here" mark inside the
   transaction). Nobody ever sees half a reservation.

3. **Repeating is harmless (idempotent, hard rule 3).** The order id is the
   reservation's primary key, and the outcome is stored **either way**: RESERVED or
   FAILED. If the same order asks again (RabbitMQ delivered OrderCreated twice), we
   find the stored outcome and give the same answer, without touching stock. Storing
   failures matters: otherwise a repeat after new stock came in could reserve copies
   for an order that was already turned down (migration 0004 explains this).

There's no "release" yet: copies reserved for an order stay reserved. Giving copies
back (e.g. for a cancelled order) is a later concern.
"""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.schemas import ReservationItem
from app.tables import FAILED, RESERVED, reservation_items, reservations, stock
from shared.events import INSUFFICIENT_STOCK


@dataclass(frozen=True)
class ReservationOutcome:
    order_id: UUID
    reserved: bool  # True = copies set aside; False = failed (see failure_reason)
    # False when this order had been handled before and we only looked up the outcome.
    created: bool
    items: list[ReservationItem] = field(default_factory=list)  # what was reserved
    failure_reason: str | None = None
    # Which book was short. Only known on the first attempt (it isn't stored).
    failure_details: list | None = None


class ReservationConflict(Exception):
    """This order id already has a reservation with different items.

    Can't happen in normal use (an order's items never change); only if someone
    publishes a hand-made OrderCreated reusing an order id.
    """


class _NotEnoughStock(Exception):
    def __init__(self, item: ReservationItem):
        super().__init__(f"not enough stock for book {item.book_id}")
        self.item = item


async def reserve_stock(
    engine: AsyncEngine, order_id: UUID, items: list[ReservationItem]
) -> ReservationOutcome:
    """Reserve `items` for `order_id`, or record why not. Safe to call again."""
    # engine.begin(): one transaction for everything below.
    async with engine.begin() as conn:
        # Claim the order id first. ON CONFLICT DO NOTHING + RETURNING gives back the
        # id if we inserted it, or nothing if this order was handled before.
        # If another request for the same order is running right now, Postgres makes
        # this wait until that one commits or rolls back, so we never both proceed.
        claimed = await conn.scalar(
            pg_insert(reservations)
            .values(order_id=order_id, status=RESERVED)
            .on_conflict_do_nothing()
            .returning(reservations.c.order_id)
        )
        if claimed is None:
            return await _stored_outcome(conn, order_id, items)

        try:
            # A savepoint: if a book is short, everything inside is undone, but the
            # outer transaction (with our claimed row) carries on.
            async with conn.begin_nested():
                await _take_copies(conn, order_id, items)
        except _NotEnoughStock as short:
            await conn.execute(
                update(reservations)
                .where(reservations.c.order_id == order_id)
                .values(status=FAILED, failure_reason=INSUFFICIENT_STOCK)
            )
            return ReservationOutcome(
                order_id=order_id,
                reserved=False,
                created=True,
                failure_reason=INSUFFICIENT_STOCK,
                failure_details=[
                    {"bookId": str(short.item.book_id), "requested": short.item.quantity}
                ],
            )

    return ReservationOutcome(order_id=order_id, reserved=True, created=True, items=items)


async def _take_copies(conn: AsyncConnection, order_id: UUID, items: list[ReservationItem]):
    # Update books in a fixed order (sorted by id). Two orders that both want books A
    # and B then lock A first, then B. Without this, one could lock A while the other
    # locks B, and each would wait for the other forever (a "deadlock"; Postgres would
    # detect it and fail one of them).
    for item in sorted(items, key=lambda i: i.book_id):
        result = await conn.execute(
            update(stock)
            .where(stock.c.book_id == item.book_id, stock.c.quantity >= item.quantity)
            .values(quantity=stock.c.quantity - item.quantity, updated_at=func.now())
        )
        # 0 rows changed: not enough copies, or no stock record for this book at all.
        # Either way the answer to the order is the same: can't have it.
        if result.rowcount != 1:
            raise _NotEnoughStock(item)

    await conn.execute(
        insert(reservation_items),
        [{"order_id": order_id, "book_id": i.book_id, "quantity": i.quantity} for i in items],
    )


async def _stored_outcome(
    conn: AsyncConnection, order_id: UUID, items: list[ReservationItem]
) -> ReservationOutcome:
    """This order was handled before: return what happened then."""
    row = (
        (await conn.execute(select(reservations).where(reservations.c.order_id == order_id)))
        .mappings()
        .one()
    )
    if row["status"] == FAILED:
        return ReservationOutcome(
            order_id=order_id,
            reserved=False,
            created=False,
            failure_reason=row["failure_reason"],
        )
    existing = await _load_items(conn, order_id)
    if not _same_items(existing, items):
        raise ReservationConflict(f"Order {order_id} already has a different reservation")
    return ReservationOutcome(order_id=order_id, reserved=True, created=False, items=existing)


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
