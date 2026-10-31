"""Seed demo books

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-31

Demo data as a migration: it runs exactly once per database (Alembic records it),
so restarts never duplicate rows. Fixed UUIDs make the books easy to reference later
when testing the cart and order services by hand.
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# A lightweight table description just for this migration. Migrations must not import
# app code: the app changes over time, but a migration has to keep working as written.
books = sa.table(
    "books",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("title", sa.Text),
    sa.column("author", sa.Text),
    sa.column("price_minor", sa.Integer),
    sa.column("currency", sa.CHAR(3)),
)

SEED_BOOKS = [
    {
        "id": uuid.UUID("11111111-1111-4111-8111-000000000001"),
        "title": "Pride and Prejudice",
        "author": "Jane Austen",
        "price_minor": 999,
        "currency": "EUR",
    },
    {
        "id": uuid.UUID("11111111-1111-4111-8111-000000000002"),
        "title": "Moby-Dick",
        "author": "Herman Melville",
        "price_minor": 1299,
        "currency": "EUR",
    },
    {
        "id": uuid.UUID("11111111-1111-4111-8111-000000000003"),
        "title": "Frankenstein",
        "author": "Mary Shelley",
        "price_minor": 849,
        "currency": "EUR",
    },
    {
        "id": uuid.UUID("11111111-1111-4111-8111-000000000004"),
        "title": "The Adventures of Sherlock Holmes",
        "author": "Arthur Conan Doyle",
        "price_minor": 1099,
        "currency": "EUR",
    },
    {
        "id": uuid.UUID("11111111-1111-4111-8111-000000000005"),
        "title": "Dracula",
        "author": "Bram Stoker",
        "price_minor": 899,
        "currency": "EUR",
    },
    {
        "id": uuid.UUID("11111111-1111-4111-8111-000000000006"),
        "title": "The Picture of Dorian Gray",
        "author": "Oscar Wilde",
        "price_minor": 799,
        "currency": "EUR",
    },
]


def upgrade() -> None:
    op.bulk_insert(books, SEED_BOOKS)


def downgrade() -> None:
    op.execute(books.delete().where(books.c.id.in_([b["id"] for b in SEED_BOOKS])))
