"""The shapes of JSON that goes in and out of the cart API.

camelCase in JSON, snake_case in Python — same convention as catalog
(see services/catalog/app/schemas.py).
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt
from pydantic.alias_generators import to_camel

# Nobody needs 1000 copies of a book in a cart; a limit keeps totals sane.
MAX_QUANTITY = 99


class SetQuantity(BaseModel):
    """Body of PUT /carts/{userId}/items/{bookId}."""

    model_config = ConfigDict(alias_generator=to_camel, extra="forbid")

    # StrictInt: 2 is fine, 2.5 and "2" are rejected. 0 is not allowed — use DELETE
    # to remove a book, so there's exactly one way to do it.
    quantity: StrictInt = Field(ge=1, le=MAX_QUANTITY)


class CartItem(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    book_id: UUID
    # Title, author and price are copied from catalog when the book is put in the
    # cart (and refreshed every time its quantity is set). See app/carts.py for why.
    title: str
    author: str
    price_minor: int
    currency: str
    quantity: int
    line_total_minor: int  # price_minor * quantity


class Cart(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    user_id: UUID
    items: list[CartItem]
    total_minor: int
    # The cart's currency, or null while the cart is empty. A cart holds books in
    # only one currency, so a single total makes sense.
    currency: str | None
