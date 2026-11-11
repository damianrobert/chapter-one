"""The shapes of JSON that goes out of the order API.

camelCase in JSON, snake_case in Python — same convention as the other services.
`POST /orders` has no body: what to order comes from the user's cart.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class OrderItem(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    book_id: UUID
    # Copies taken at checkout (hard rule 2): what the customer saw and paid.
    title: str
    author: str
    unit_price_minor: int
    quantity: int
    line_total_minor: int  # unit_price_minor * quantity


class Order(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    user_id: UUID
    status: str  # PENDING | CONFIRMED | CANCELLED
    cancel_reason: str | None
    items: list[OrderItem]
    total_minor: int
    currency: str
    created_at: datetime
    updated_at: datetime


class OrderPage(BaseModel):
    """GET /orders response: one page of the user's orders, newest first."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[Order]
    total: int  # how many orders this user has in total, across all pages
    limit: int
    offset: int
