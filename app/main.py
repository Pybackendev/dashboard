import asyncio
import logging
import os
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlalchemy.orm import Session
from models import Base, Order, OrderItem, Product, SyncLog
from config import settings
from datetime import datetime, timedelta
from sync import sync, engine

log = logging.getLogger("app")
app = FastAPI(title="Shop dashboard")

# Отдаём фронтенд (в Docker /frontend, локально ../frontend)
FRONTEND_DIR = settings.frontend_dir if os.path.isdir(settings.frontend_dir) \
    else os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/static/index.html")

@app.get("/api/stats")
def get_stats(days: int = 7):
    since = datetime.utcnow() - timedelta(days=days)
    with Session(engine) as db:
        orders = db.query(Order).filter(Order.created_at >= since).all()
        total_revenue = sum(o.total for o in orders)
        avg_check = total_revenue / len(orders) if orders else 0

        # Выручка по дням
        revenue_by_day = {}
        for o in orders:
            day = o.created_at.strftime("%Y-%m-%d")
            revenue_by_day[day] = revenue_by_day.get(day, 0) + o.total

        # Топ-5 товаров
        top_products = (
            db.query(
                OrderItem.product_name,
                func.sum(OrderItem.quantity).label("total_qty")
            )
            .join(Order, Order.id == OrderItem.order_id)
            .filter(Order.created_at >= since)
            .group_by(OrderItem.product_name)
            .order_by(func.sum(OrderItem.quantity).desc())
            .limit(5)
            .all()
        )

        # Предыдущий период такой же длины (для сравнения на карточках)
        prev_since = since - timedelta(days=days)
        prev_orders = (
            db.query(Order)
            .filter(Order.created_at >= prev_since, Order.created_at < since)
            .all()
        )
        prev_revenue = sum(o.total for o in prev_orders)
        prev_avg = prev_revenue / len(prev_orders) if prev_orders else 0

        # Время последней успешной синхронизации
        last_sync = (
            db.query(SyncLog)
            .filter(SyncLog.status == "ok")
            .order_by(SyncLog.id.desc())
            .first()
        )

        return {
            "orders_count": len(orders),
            "total_revenue": round(total_revenue, 2),
            "avg_check": round(avg_check, 2),
            "revenue_by_day": revenue_by_day,
            "top_products": [
                {"name": p.product_name, "qty": int(p.total_qty)}
                for p in top_products
            ],
            "prev": {
                "orders_count": len(prev_orders),
                "total_revenue": round(prev_revenue, 2),
                "avg_check": round(prev_avg, 2),
            },
            "last_sync": last_sync.synced_at.isoformat() if last_sync else None,
        }

@app.get("/api/orders")
def get_orders(limit: int = 10):
    limit = max(1, min(limit, 100))
    with Session(engine) as db:
        rows = db.query(Order).order_by(Order.created_at.desc()).limit(limit).all()
        return [
            {
                "id": o.opencart_id,
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "customer": o.customer_name,
                "total": o.total,
                "status": o.status,
            }
            for o in rows
        ]

@app.get("/api/products")
def get_products():
    with Session(engine) as db:
        products = db.query(Product).order_by(Product.quantity).limit(20).all()
        return [
            {"name": p.name, "quantity": p.quantity, "price": p.price}
            for p in products
        ]

@app.post("/api/sync")
def run_sync():
    try:
        new = sync()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Sync failed: {str(e)[:200]}")
    return {"status": "ok", "new_orders": new}


def _safe_sync():
    try:
        sync()
    except Exception:
        pass  # ошибка уже залогирована и записана в sync_log


async def _periodic_sync():
    while True:
        await asyncio.sleep(max(1, settings.sync_interval_minutes) * 60)
        await asyncio.to_thread(_safe_sync)


@app.on_event("startup")
async def startup():
    # Ждём, пока поднимется Postgres, и создаём таблицы
    for attempt in range(30):
        try:
            Base.metadata.create_all(engine)
            break
        except Exception as e:
            log.warning("postgres not ready (%s), retry %s/30", type(e).__name__, attempt + 1)
            time.sleep(2)
    else:
        raise RuntimeError("Postgres is not reachable")

    # Первая синхронизация не должна валить приложение (OpenCart может быть ещё не установлен)
    await asyncio.to_thread(_safe_sync)
    asyncio.create_task(_periodic_sync())
