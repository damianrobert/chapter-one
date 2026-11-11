"""The shapes of JSON that goes in and out of the inventory API.

JSON is camelCase (`bookId`), Python is snake_case; `alias_generator=to_camel`
converts between them, like in the other services.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from pydantic.alias_generators import to_camel

# Same upper limit as the cart's quantity, so anything a cart allows can be reserved.
MAX_ITEM_QUANTITY = 99
# Generous, but stops a single request from being absurdly large.
MAX_STOCK = 1_000_000
MAX_ITEMS_PER_RESERVATION = 100


class Stock(BaseModel):
    """GET/PUT /stock/{bookId} response."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    book_id: UUID
    quantity: int


class StockUpdate(BaseModel):
    """Body of PUT /stock/{bookId}: the new number of copies (not a change by +/-)."""

    model_config = ConfigDict(alias_generator=to_camel, extra="forbid")

    quantity: StrictInt = Field(ge=0, le=MAX_STOCK)


class ReservationItem(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    book_id: UUID
    quantity: StrictInt = Field(ge=1, le=MAX_ITEM_QUANTITY)


class ReservationRequest(BaseModel):
    """The items of an OrderCreated event, checked (app/consumer.py).

    Was the body of step 3's `PUT /reservations/{orderId}`; the endpoint is gone, the
    checks are still wanted.
    """

    model_config = ConfigDict(alias_generator=to_camel, extra="forbid")

    items: list[ReservationItem] = Field(min_length=1, max_length=MAX_ITEMS_PER_RESERVATION)

    @field_validator("items")
    @classmethod
    def each_book_once(cls, items: list[ReservationItem]) -> list[ReservationItem]:
        # One line per book. Two lines for the same book would make the "is this the
        # same order again?" check (see reservations.py) ambiguous.
        book_ids = [item.book_id for item in items]
        if len(book_ids) != len(set(book_ids)):
            raise ValueError("each bookId may appear only once")
        return items
