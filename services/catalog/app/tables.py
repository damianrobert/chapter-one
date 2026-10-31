"""Python description of catalog_db's tables, used to build SQL queries.

The migrations in migrations/versions/ are the source of truth for the schema: they
create and change the real tables. This file only *describes* those tables so we can
write queries like `select(books).where(books.c.id == ...)` instead of raw SQL
strings. It never creates anything. When a migration changes a table, update the
description here to match.

We use SQLAlchemy "Core" (tables + queries) rather than the ORM (classes mapped to
rows): the SQL that runs stays easy to see, and there's no hidden lazy loading.
"""

from sqlalchemy import CHAR, TIMESTAMP, Column, FetchedValue, Integer, MetaData, Table, Text
from sqlalchemy.dialects.postgresql import UUID

metadata = MetaData()

books = Table(
    "books",
    metadata,
    # FetchedValue() tells SQLAlchemy "the database fills this in" (the migration set
    # defaults: gen_random_uuid() and now()), so it's fine to INSERT without them.
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=FetchedValue()),
    Column("title", Text, nullable=False),
    Column("author", Text, nullable=False),
    Column("price_minor", Integer, nullable=False),  # cents, never a float
    Column("currency", CHAR(3), nullable=False),  # ISO 4217, e.g. "EUR"
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=FetchedValue()),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=FetchedValue()),
)
