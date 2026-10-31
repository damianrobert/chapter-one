"""The shapes of JSON that goes in and out of the books API.

Pydantic models do two jobs: FastAPI validates incoming JSON against them (bad input
gets a 400 before our code runs), and they define exactly which fields we return.

JSON field names are camelCase (`priceMinor`), matching the event envelope
(`eventId`, `occurredAt`) so the whole system uses one style. Python code keeps
snake_case; `alias_generator=to_camel` converts between the two.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints
from pydantic.alias_generators import to_camel

# Largest value a Postgres INTEGER column can hold. Without this limit, a huge price
# would pass validation and then crash the INSERT with a 500 instead of a clean 400.
MAX_PRICE_MINOR = 2_147_483_647

# Text that is trimmed of surrounding spaces and can't be empty afterwards.
NonEmptyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]


class BookCreate(BaseModel):
    """Body of POST /books."""

    # extra="forbid": unknown fields are an error. A typo like "price" instead of
    # "priceMinor" is reported, instead of being silently ignored.
    model_config = ConfigDict(alias_generator=to_camel, extra="forbid")

    title: NonEmptyText
    author: NonEmptyText
    # StrictInt: only real JSON integers. Rejects 9.99 *and* 9.0 and "999" — for money
    # we don't want any guessing about what the client meant.
    price_minor: StrictInt = Field(ge=0, le=MAX_PRICE_MINOR)
    currency: str = Field(pattern=r"^[A-Z]{3}$", examples=["EUR"])


class Book(BaseModel):
    """A book as returned by the API."""

    # populate_by_name: lets us build it from a DB row, whose keys are snake_case.
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    title: str
    author: str
    price_minor: int
    currency: str
    created_at: datetime
    updated_at: datetime


class BookPage(BaseModel):
    """One page of results from GET /books."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[Book]
    total: int  # how many books match in total, across all pages
    limit: int
    offset: int
