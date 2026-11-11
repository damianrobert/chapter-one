"""Seed stock for the demo books

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-11

Starting stock for catalog's six demo books (catalog migration 0002 uses the same
fixed ids). The numbers are small on purpose, so "out of stock" is easy to try:
Dracula has only 2 copies and Dorian Gray has none.
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# A lightweight table description just for this migration (migrations must not import
# app code, which changes over time).
stock = sa.table(
    "stock",
    sa.column("book_id", UUID(as_uuid=True)),
    sa.column("quantity", sa.Integer),
)

# Book ids from catalog's seed migration; the comment says which book each one is.
SEED_STOCK = [
    {"book_id": uuid.UUID("11111111-1111-4111-8111-000000000001"), "quantity": 10},  # Pride…
    {"book_id": uuid.UUID("11111111-1111-4111-8111-000000000002"), "quantity": 10},  # Moby-Dick
    {"book_id": uuid.UUID("11111111-1111-4111-8111-000000000003"), "quantity": 5},  # Frankenstein
    {"book_id": uuid.UUID("11111111-1111-4111-8111-000000000004"), "quantity": 5},  # Sherlock
    {"book_id": uuid.UUID("11111111-1111-4111-8111-000000000005"), "quantity": 2},  # Dracula
    {"book_id": uuid.UUID("11111111-1111-4111-8111-000000000006"), "quantity": 0},  # Dorian Gray
]


def upgrade() -> None:
    op.bulk_insert(stock, SEED_STOCK)


def downgrade() -> None:
    op.execute(stock.delete().where(stock.c.book_id.in_([s["book_id"] for s in SEED_STOCK])))
