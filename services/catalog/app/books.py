"""The books API: list/search, get one, create.

Each endpoint borrows a connection from the engine's pool for the duration of the
request and gives it back afterwards (`async with engine.connect()`), so a handful
of pooled connections can serve many requests.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.errors import ApiError
from app.schemas import Book, BookCreate, BookPage
from app.tables import books

router = APIRouter(prefix="/books", tags=["books"])


def get_engine(request: Request) -> AsyncEngine:
    # A FastAPI "dependency": endpoints ask for the engine and FastAPI calls this to
    # supply it. Tests could swap it out without touching the endpoints.
    return request.app.state.engine


Engine = Annotated[AsyncEngine, Depends(get_engine)]


def escape_like(text: str) -> str:
    r"""Make `%` and `_` in user input literal characters in a LIKE pattern.

    In SQL LIKE, `%` means "anything" and `_` means "any one character". Without
    escaping, searching for "100%" would match every title starting with "100".
    The backslash itself is escaped first, because it's the escape character.
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=BookPage)
async def list_books(
    engine: Engine,
    q: Annotated[str | None, Query(max_length=200, description="Search title or author")] = None,
    # Pagination: return `limit` books, skipping the first `offset`. The upper limit
    # stops one request from asking for the whole table at once.
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BookPage:
    conditions = []
    if q and q.strip():
        # Case-insensitive "contains" search on title OR author. The value goes in as a
        # bound parameter, never pasted into the SQL text, so it can't inject SQL.
        pattern = f"%{escape_like(q.strip())}%"
        conditions.append(
            or_(
                books.c.title.ilike(pattern, escape="\\"),
                books.c.author.ilike(pattern, escape="\\"),
            )
        )

    count_query = select(func.count()).select_from(books).where(*conditions)
    # Order by title, then id. The id tie-breaker gives a stable order even for two
    # books with the same title, so paging never skips or repeats a book.
    page_query = (
        select(books)
        .where(*conditions)
        .order_by(books.c.title, books.c.id)
        .limit(limit)
        .offset(offset)
    )

    async with engine.connect() as conn:
        total = await conn.scalar(count_query)
        rows = (await conn.execute(page_query)).mappings().all()

    return BookPage(
        items=[Book.model_validate(dict(row)) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{book_id}", response_model=Book)
async def get_book(book_id: UUID, engine: Engine) -> Book:
    # `book_id: UUID` means FastAPI rejects a non-UUID like /books/abc with a 400
    # before this code runs.
    async with engine.connect() as conn:
        row = (await conn.execute(select(books).where(books.c.id == book_id))).mappings().first()

    if row is None:
        raise ApiError(404, "BOOK_NOT_FOUND", f"Book {book_id} not found")
    return Book.model_validate(dict(row))


@router.post("", response_model=Book, status_code=201)
async def create_book(body: BookCreate, response: Response, engine: Engine) -> Book:
    # The DB generates the id and timestamps. RETURNING hands back the full new row in
    # the same round trip, so we don't need a second SELECT.
    # engine.begin() opens a transaction and commits it when the block ends (or rolls
    # back if an error is raised inside).
    query = (
        insert(books)
        .values(
            title=body.title,
            author=body.author,
            price_minor=body.price_minor,
            currency=body.currency,
        )
        .returning(*books.c)
    )
    async with engine.begin() as conn:
        result = await conn.execute(query)
        row = result.mappings().one()

    book = Book.model_validate(dict(row))
    # 201 Created + Location header pointing at the new resource (REST convention).
    # It's relative to this service; the gateway will add its /api prefix later.
    response.headers["Location"] = f"/books/{book.id}"
    return book
