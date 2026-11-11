"""Stock levels: look one up, or set it.

Setting stock is a plain "the shelf now holds N copies" (not "+3"), so repeating the
same PUT is harmless. Nothing routes here from the gateway yet; locally you can call
it on port 8003 to stock a newly created book.
"""

from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.db import Engine
from app.errors import ApiError
from app.schemas import Stock, StockUpdate
from app.tables import stock

router = APIRouter(prefix="/stock", tags=["stock"])


@router.get("/{book_id}", response_model=Stock)
async def get_stock(book_id: UUID, engine: Engine) -> Stock:
    query = select(stock.c.book_id, stock.c.quantity).where(stock.c.book_id == book_id)
    async with engine.connect() as conn:
        row = (await conn.execute(query)).mappings().first()

    if row is None:
        # Inventory doesn't know whether the book exists in catalog (it can't look);
        # it only knows it has no stock record for this id.
        raise ApiError(404, "STOCK_NOT_FOUND", f"No stock record for book {book_id}")
    return Stock.model_validate(dict(row))


@router.put("/{book_id}", response_model=Stock)
async def set_stock(book_id: UUID, body: StockUpdate, engine: Engine) -> Stock:
    # "Upsert": insert a new row, or if one exists for this book, update it instead.
    # One SQL statement, so there's no gap between "does it exist?" and "write it".
    query = insert(stock).values(book_id=book_id, quantity=body.quantity)
    query = query.on_conflict_do_update(
        index_elements=[stock.c.book_id],
        set_={"quantity": query.excluded.quantity, "updated_at": func.now()},
    ).returning(stock.c.book_id, stock.c.quantity)

    async with engine.begin() as conn:
        row = (await conn.execute(query)).mappings().one()
    return Stock.model_validate(dict(row))
