"""Create orders and order_items tables

Revision ID: 0001
Revises:
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        # Who placed it (the X-User-ID the gateway forwards). No foreign key: there is
        # no users table anywhere yet, and it wouldn't be in this database anyway.
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        # PENDING -> CONFIRMED or CANCELLED, nothing else (see CLAUDE.md). The CHECK
        # stops typos like "CONFIRMD" from ever reaching the table.
        sa.Column("status", sa.Text, nullable=False),
        # Why it was cancelled (e.g. "INSUFFICIENT_STOCK"); empty otherwise.
        sa.Column("cancel_reason", sa.Text, nullable=True),
        # Sum of the items, worked out once at checkout and stored. BIGINT because a
        # total can grow beyond what a single price (INTEGER) holds.
        sa.Column("total_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'CONFIRMED', 'CANCELLED')", name="ck_orders_status"
        ),
        sa.CheckConstraint("total_minor >= 0", name="ck_orders_total_non_negative"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_orders_currency_iso"),
        # A reason only makes sense on a cancelled order.
        sa.CheckConstraint(
            "cancel_reason IS NULL OR status = 'CANCELLED'",
            name="ck_orders_reason_only_if_cancelled",
        ),
    )
    # "Show me my orders, newest first" is the common question; this index answers it
    # without scanning every order.
    op.create_index("ix_orders_user_id_created_at", "orders", ["user_id", "created_at"])

    op.create_table(
        "order_items",
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Catalog's book id: kept so we know which book it was, but never used to look
        # the book up again for this order (hard rule 2).
        sa.Column("book_id", UUID(as_uuid=True), nullable=False),
        # COPIES taken at checkout (hard rule 2). If catalog later renames the book or
        # changes its price, this order still shows what the customer actually bought
        # and paid. Catalog's database isn't reachable from here anyway (hard rule 1).
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("author", sa.Text, nullable=False),
        sa.Column("unit_price_minor", sa.Integer, nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.PrimaryKeyConstraint("order_id", "book_id"),
        sa.CheckConstraint("unit_price_minor >= 0", name="ck_order_items_price_non_negative"),
        sa.CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
    )


def downgrade() -> None:
    op.drop_table("order_items")
    op.drop_index("ix_orders_user_id_created_at", table_name="orders")
    op.drop_table("orders")
