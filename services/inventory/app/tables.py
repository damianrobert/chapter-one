"""Python description of inventory_db's tables, used to build SQL queries.

The migrations in migrations/versions/ are the source of truth for the schema; this
file only *describes* the tables so queries can be written in Python. When a
migration changes a table, update the description here to match.
"""

from sqlalchemy import TIMESTAMP, Column, FetchedValue, Integer, MetaData, Table
from sqlalchemy.dialects.postgresql import UUID

metadata = MetaData()

stock = Table(
    "stock",
    metadata,
    # The catalog's book id. NOT a foreign key: the books table lives in another
    # service's database, which inventory can't see (hard rule 1).
    Column("book_id", UUID(as_uuid=True), primary_key=True),
    Column("quantity", Integer, nullable=False),  # copies available to sell
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=FetchedValue()),
)
