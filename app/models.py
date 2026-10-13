from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, Text, Numeric, Boolean, Index
from datetime import UTC, datetime

class Base(DeclarativeBase):
    pass

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    opencart_id = Column(Integer, unique=True)   # id из OpenCart
    created_at = Column(DateTime(timezone=True), nullable=False)
    source_modified_at = Column(DateTime(timezone=True))
    total = Column(Numeric(12, 2), nullable=False)
    status = Column(String(50))
    customer_name = Column(String(100))
    is_deleted = Column(Boolean, nullable=False, default=False, server_default="false")


Index("ix_orders_created_at", Order.created_at)

class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    product_name = Column(String(200))
    quantity = Column(Integer)
    price = Column(Numeric(12, 2), nullable=False)

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    opencart_id = Column(Integer, unique=True)
    name = Column(String(200))
    quantity = Column(Integer)   # остаток
    price = Column(Numeric(12, 2), nullable=False)
    is_deleted = Column(Boolean, nullable=False, default=False, server_default="false")

class SyncLog(Base):
    __tablename__ = "sync_log"

    id = Column(Integer, primary_key=True)
    synced_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    orders_synced = Column(Integer)
    status = Column(String(20))   # ok / error
    error_message = Column(Text, nullable=True)


class SyncState(Base):
    __tablename__ = "sync_state"

    id = Column(Integer, primary_key=True)
    last_order_modified = Column(DateTime(timezone=True))
    last_full_sync_at = Column(DateTime(timezone=True))


class DailySnapshot(Base):
    __tablename__ = "daily_snapshots"

    snapshot_date = Column(Date, primary_key=True)
    captured_at = Column(DateTime(timezone=True), nullable=False)
    low_stock_count = Column(Integer, nullable=False)
    product_count = Column(Integer, nullable=False)


class BotDelivery(Base):
    __tablename__ = "bot_deliveries"

    id = Column(Integer, primary_key=True)
    delivery_key = Column(String(100), nullable=False, unique=True)
    sent_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

