"""Create books table

Revision ID: 0001
Revises:
Create Date: 2026-10-31
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
        "books",
        # UUID ids (API convention). gen_random_uuid() is built into Postgres 13+,
        # so the DB can generate ids itself if the app doesn't supply one.
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("title", sa.Text, nullable=False),
        # Author is a plain column for now. A separate authors table (many-to-many)
        # would be more "correct" but adds joins without teaching anything new here.
        sa.Column("author", sa.Text, nullable=False),
        # Money: integer minor units (cents) + ISO 4217 currency code. Never floats:
        # 0.1 + 0.2 != 0.3 in floating point, and money must add up exactly.
        sa.Column("price_minor", sa.Integer, nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        # Constraints in the DB, not only in app code: they hold even if a bug or a
        # manual SQL session tries to write bad data.
        sa.CheckConstraint("price_minor >= 0", name="ck_books_price_non_negative"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_books_currency_iso"),
    )


def downgrade() -> None:
    op.drop_table("books")
