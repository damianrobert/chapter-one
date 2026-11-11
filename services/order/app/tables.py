"""Python description of order_db's tables, used to build SQL queries.

The migrations in migrations/versions/ are the source of truth for the schema; this
file only *describes* the tables so queries can be written in Python. When a
migration changes a table, update the description here to match.
"""

from sqlalchemy import (
    CHAR,
    TIMESTAMP,
    BigInteger,
    Column,
    FetchedValue,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID

metadata = MetaData()

# The only statuses an order can have (CLAUDE.md, "Checkout flow").
PENDING = "PENDING"
CONFIRMED = "CONFIRMED"
CANCELLED = "CANCELLED"

orders = Table(
    "orders",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=FetchedValue()),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("status", Text, nullable=False),
    Column("cancel_reason", Text, nullable=True),
    Column("total_minor", BigInteger, nullable=False),
    Column("currency", CHAR(3), nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=FetchedValue()),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=FetchedValue()),
)

# Each line of an order, with the title and price COPIED at checkout (hard rule 2).
order_items = Table(
    "order_items",
    metadata,
    Column("order_id", UUID(as_uuid=True), ForeignKey("orders.id"), primary_key=True),
    Column("book_id", UUID(as_uuid=True), primary_key=True),
    Column("title", Text, nullable=False),
    Column("author", Text, nullable=False),
    Column("unit_price_minor", Integer, nullable=False),
    Column("quantity", Integer, nullable=False),
)
