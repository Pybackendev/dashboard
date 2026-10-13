"""Initial schema."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opencart_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_modified_at", sa.DateTime(timezone=True)),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(50)),
        sa.Column("customer_name", sa.String(100)),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.UniqueConstraint("opencart_id", name="uq_orders_opencart_id"))
    op.create_index("ix_orders_created_at", "orders", ["created_at"])
    op.create_table("order_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id")),
        sa.Column("product_name", sa.String(200)),
        sa.Column("quantity", sa.Integer()),
        sa.Column("price", sa.Numeric(12, 2), nullable=False))
    op.create_table("products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opencart_id", sa.Integer()), sa.Column("name", sa.String(200)),
        sa.Column("quantity", sa.Integer()), sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.UniqueConstraint("opencart_id", name="uq_products_opencart_id"))
    op.create_table("sync_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("orders_synced", sa.Integer()), sa.Column("status", sa.String(20)),
        sa.Column("error_message", sa.Text()))
    op.create_table("sync_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_order_modified", sa.DateTime(timezone=True)),
        sa.Column("last_full_sync_at", sa.DateTime(timezone=True)))

def downgrade():
    op.drop_table("sync_state")
    op.drop_table("sync_log")
    op.drop_table("products")
    op.drop_table("order_items")
    op.drop_index("ix_orders_created_at", table_name="orders")
    op.drop_table("orders")
