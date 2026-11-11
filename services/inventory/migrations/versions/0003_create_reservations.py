"""Create reservations tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-11

A reservation records "these copies were taken out of stock for this order". Keeping it
(instead of only lowering `stock.quantity`) lets inventory recognise a repeated request
for the same order and not take the copies a second time.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reservations",
        # The order's id (from the order service). One reservation per order: the
        # primary key makes the database itself refuse a second one.
        sa.Column("order_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "reservation_items",
        # A foreign key is fine here: both tables are in inventory's own database.
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("reservations.order_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("book_id", UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.PrimaryKeyConstraint("order_id", "book_id"),
        sa.CheckConstraint("quantity > 0", name="ck_reservation_items_quantity_positive"),
    )


def downgrade() -> None:
    op.drop_table("reservation_items")
    op.drop_table("reservations")
