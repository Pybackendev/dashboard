"""Keep a daily low-stock history for dashboard trends."""
from alembic import op
import sqlalchemy as sa

revision = "0003_daily_snapshots"
down_revision = "0002_product_soft_delete"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "daily_snapshots",
        sa.Column("snapshot_date", sa.Date(), primary_key=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("low_stock_count", sa.Integer(), nullable=False),
        sa.Column("product_count", sa.Integer(), nullable=False),
    )


def downgrade():
    op.drop_table("daily_snapshots")
