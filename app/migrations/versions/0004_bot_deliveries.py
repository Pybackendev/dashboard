"""Track daily Telegram reports to avoid duplicates after restarts."""
from alembic import op
import sqlalchemy as sa

revision = "0004_bot_deliveries"
down_revision = "0003_daily_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bot_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("delivery_key", sa.String(length=100), nullable=False, unique=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("bot_deliveries")
