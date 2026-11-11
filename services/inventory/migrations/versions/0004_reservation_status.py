"""Remember failed reservations too

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-11

Until now only successful reservations were stored; a failed attempt left nothing
behind, so the same order could try again later (step 3's HTTP design).

With events (step 4) that's a bug. RabbitMQ may deliver the same OrderCreated twice
("at least once"). If the first delivery failed for lack of stock and new stock came
in before the second, the second would succeed: inventory would say "reserved" for an
order it already turned down, and the copies would be locked away for an order that
order had cancelled.

So every order's outcome is written down, success or failure. A repeat then always
gets the same answer (hard rule 3: consumers must be idempotent).
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows are all successful reservations, hence the temporary default.
    op.add_column(
        "reservations",
        sa.Column("status", sa.Text, nullable=False, server_default="RESERVED"),
    )
    op.alter_column("reservations", "status", server_default=None)
    op.add_column("reservations", sa.Column("failure_reason", sa.Text, nullable=True))
    op.create_check_constraint(
        "ck_reservations_status", "reservations", "status IN ('RESERVED', 'FAILED')"
    )
    # A reason exactly when it failed.
    op.create_check_constraint(
        "ck_reservations_failure_reason",
        "reservations",
        "(status = 'FAILED') = (failure_reason IS NOT NULL)",
    )


def downgrade() -> None:
    op.execute("DELETE FROM reservations WHERE status = 'FAILED'")
    op.drop_constraint("ck_reservations_failure_reason", "reservations")
    op.drop_constraint("ck_reservations_status", "reservations")
    op.drop_column("reservations", "failure_reason")
    op.drop_column("reservations", "status")
