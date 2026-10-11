from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
from datetime import datetime

class Base(DeclarativeBase):
    pass

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    opencart_id = Column(Integer, unique=True)   # id из OpenCart
    created_at = Column(DateTime)
    total = Column(Float)
    status = Column(String(50))
    customer_name = Column(String(100))

class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    product_name = Column(String(200))
    quantity = Column(Integer)
    price = Column(Float)

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    opencart_id = Column(Integer, unique=True)
    name = Column(String(200))
    quantity = Column(Integer)   # остаток
    price = Column(Float)

class SyncLog(Base):
    __tablename__ = "sync_log"

    id = Column(Integer, primary_key=True)
    synced_at = Column(DateTime, default=datetime.utcnow)
    orders_synced = Column(Integer)
    status = Column(String(20))   # ok / error
    error_message = Column(Text, nullable=True)

