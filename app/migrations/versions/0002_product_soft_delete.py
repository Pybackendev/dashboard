"""Track products removed from the source shop."""
from alembic import op
import sqlalchemy as sa

revision = "0002_product_soft_delete"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("products", sa.Column(
        "is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False
    ))


def downgrade():
    op.drop_column("products", "is_deleted")
