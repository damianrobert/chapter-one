"""Create stock table

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
        "stock",
        # One row per book: how many copies we can still sell.
        # book_id is the catalog's id, but there's no foreign key to a books table:
        # that table is in catalog_db, a different database (hard rule 1). Inventory
        # only knows book ids, not titles or prices.
        sa.Column("book_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        # The database itself refuses negative stock. Even if a bug ever tried to sell
        # more copies than exist, this check would make that UPDATE fail.
        sa.CheckConstraint("quantity >= 0", name="ck_stock_quantity_non_negative"),
    )


def downgrade() -> None:
    op.drop_table("stock")
