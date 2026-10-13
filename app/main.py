import asyncio
import csv
import io
import logging
import os
import random
import time
from datetime import UTC, date, datetime, time as day_time, timedelta
from decimal import Decimal
from html import escape

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException
from fastapi import Depends, Request
from fastapi.responses import RedirectResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Integer, String, cast, func, text
from sqlalchemy.orm import Session

from config import settings
from events import event_stream, publish, subscriber_count
from models import BotDelivery, DailySnapshot, Order, OrderItem, Product, SyncLog
from simulate import create_demo_order
from sync import SyncResult, engine, sync
from time_utils import shop_zone
from telegram_bot import notify_low_stock, notify_new_orders, run_polling, send_to_allowed
from security import ActionRateLimiter, enforce_action_token, enforce_rate_limit

log = logging.getLogger("app")
app = FastAPI(title="Shop dashboard")
simulate_limiter = ActionRateLimiter(
    settings.simulate_cooldown_seconds, settings.simulate_limit_per_hour,
)


def _require_action_token(request: Request) -> bool:
    return enforce_action_token(request, settings.demo_action_token)

FRONTEND_DIR = settings.frontend_dir if os.path.isdir(settings.frontend_dir) \
    else os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/static/index.html")


@app.get("/api/config")
def get_config():
    return {"currency": settings.currency, "timezone": settings.timezone,
            "github_url": settings.github_url, "app_version": settings.app_version,
            "demo_mode": settings.demo_mode}


def _date_window(days: int, date_from: date | None = None, date_to: date | None = None):
    if (date_from is None) != (date_to is None):
        raise HTTPException(status_code=422, detail="date_from и date_to нужно передавать вместе")
    zone = shop_zone()
    if date_from is not None and date_to is not None:
        if date_to < date_from:
            raise HTTPException(status_code=422, detail="date_to не может быть раньше date_from")
        start_day, end_day = date_from, date_to + timedelta(days=1)
    else:
        today = datetime.now(zone).date()
        end_day = today + timedelta(days=1)
        start_day = end_day - timedelta(days=max(1, min(days, 365)))
    span = (end_day - start_day).days
    start_local = datetime.combine(start_day, day_time.min, tzinfo=zone)
    end_local = datetime.combine(end_day, day_time.min, tzinfo=zone)
    prev_start_local = datetime.combine(start_day - timedelta(days=span), day_time.min, tzinfo=zone)
    return (start_local.astimezone(UTC), end_local.astimezone(UTC),
            prev_start_local.astimezone(UTC), start_day, end_day - timedelta(days=1))


def _contributing(query, start, end):
    query = query.filter(
        Order.is_deleted.is_(False), Order.created_at >= start, Order.created_at < end
    )
    excluded = [status.casefold() for status in settings.excluded_statuses]
    if excluded:
        query = query.filter(func.lower(Order.status).notin_(excluded))
    return query


def _order_filter(query, *, status=None, q=None, date_from=None, date_to=None):
    query = query.filter(Order.is_deleted.is_(False))
    if status:
        query = query.filter(func.lower(Order.status) == status.casefold())
    if q:
        term = f"%{q.strip()}%"
        query = query.filter(
            Order.customer_name.ilike(term) | cast(Order.opencart_id, String).ilike(term)
        )
    if date_from:
        start = datetime.combine(date_from, day_time.min, tzinfo=shop_zone()).astimezone(UTC)
        query = query.filter(Order.created_at >= start)
    if date_to:
        stop = datetime.combine(date_to + timedelta(days=1), day_time.min,
                                tzinfo=shop_zone()).astimezone(UTC)
        query = query.filter(Order.created_at < stop)
    if date_from and date_to and date_to < date_from:
        raise HTTPException(status_code=422, detail="date_to не может быть раньше date_from")
    return query


@app.get("/api/stats")
def get_stats(days: int = 7, date_from: date | None = None, date_to: date | None = None):
    since, end, prev_since, start_day, last_day = _date_window(days, date_from, date_to)
    with Session(engine) as db:
        current = _contributing(db.query(func.count(Order.id), func.coalesce(func.sum(Order.total), 0)), since, end).one()
        orders_count, revenue = int(current[0]), current[1]
        avg_check = revenue / orders_count if orders_count else 0

        day_expr = func.date(func.timezone(settings.timezone, Order.created_at))
        daily = (
            _contributing(db.query(day_expr, func.count(Order.id), func.sum(Order.total)), since, end)
            .group_by(day_expr).order_by(day_expr).all()
        )
        orders_by_day = {day.isoformat(): int(count) for day, count, _ in daily}
        revenue_by_day = {day.isoformat(): round(float(amount), 2) for day, _, amount in daily}
        average_check_by_day = {
            day.isoformat(): round(float(amount / count), 2)
            for day, count, amount in daily if count
        }
        stock_snapshots = (db.query(DailySnapshot).filter(
            DailySnapshot.snapshot_date >= start_day,
            DailySnapshot.snapshot_date <= last_day,
        ).order_by(DailySnapshot.snapshot_date).all())

        top_query = (
            db.query(OrderItem.product_name,
                     func.sum(OrderItem.quantity).label("total_qty"))
            .join(Order, Order.id == OrderItem.order_id)
            .filter(Order.is_deleted.is_(False), Order.created_at >= since, Order.created_at < end)
        )
        top_query = _contributing(top_query, since, end)
        top_products = (
            top_query.group_by(OrderItem.product_name)
            .order_by(func.sum(OrderItem.quantity).desc()).limit(5).all()
        )

        previous = _contributing(
            db.query(func.count(Order.id), func.coalesce(func.sum(Order.total), 0)),
            prev_since, since,
        ).one()
        prev_count, prev_revenue = int(previous[0]), previous[1]
        prev_avg = prev_revenue / prev_count if prev_count else 0

        last_sync = (
            db.query(SyncLog).filter(SyncLog.status == "ok")
            .order_by(SyncLog.id.desc()).first()
        )

        return {
            "orders_count": orders_count,
            "total_revenue": round(float(revenue), 2),
            "avg_check": round(float(avg_check), 2),
            "revenue_by_day": revenue_by_day,
            "orders_by_day": orders_by_day,
            "average_check_by_day": average_check_by_day,
            "low_stock_by_day": {
                row.snapshot_date.isoformat(): row.low_stock_count for row in stock_snapshots
            },
            "top_products": [
                {"name": row.product_name, "qty": int(row.total_qty)} for row in top_products
            ],
            "prev": {
                "orders_count": prev_count,
                "total_revenue": round(float(prev_revenue), 2),
                "avg_check": round(float(prev_avg), 2),
            },
            "last_sync": last_sync.synced_at.isoformat() if last_sync else None,
            "date_from": start_day.isoformat(), "date_to": last_day.isoformat(),
            "previous_date_from": (start_day - (last_day - start_day + timedelta(days=1))).isoformat(),
        }


@app.get("/api/orders")
def get_orders(status: str | None = None, q: str | None = None,
               date_from: date | None = None, date_to: date | None = None,
               sort: str = "created_at", order: str = "desc",
               page: int = 1, page_size: int = 10):
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    sort_columns = {"id": Order.opencart_id, "created_at": Order.created_at,
                    "total": Order.total, "status": Order.status,
                    "customer": Order.customer_name}
    if sort not in sort_columns or order.lower() not in {"asc", "desc"}:
        raise HTTPException(status_code=422, detail="Недопустимая сортировка")
    with Session(engine) as db:
        query = _order_filter(db.query(Order), status=status, q=q,
                              date_from=date_from, date_to=date_to)
        total = query.count()
        column = sort_columns[sort]
        query = query.order_by(column.desc() if order.lower() == "desc" else column.asc())
        rows = query.offset((page - 1) * page_size).limit(page_size).all()
        return {"items": [_order_json(row) for row in rows],
                "total": total, "page": page, "page_size": page_size}


@app.get("/api/order-statuses")
def get_order_statuses():
    with Session(engine) as db:
        return [row[0] for row in db.query(Order.status).filter(
            Order.is_deleted.is_(False), Order.status.isnot(None)
        ).distinct().order_by(Order.status).all()]


def _order_json(order):
    return {"id": order.opencart_id,
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "customer": order.customer_name, "total": round(float(order.total), 2),
            "status": order.status}


@app.get("/api/orders/{order_id}")
def get_order(order_id: int):
    with Session(engine) as db:
        order = db.query(Order).filter(
            Order.opencart_id == order_id, Order.is_deleted.is_(False)
        ).first()
        if order is None:
            raise HTTPException(status_code=404, detail="Заказ не найден")
        items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
        result = _order_json(order)
        result["items"] = [{"name": item.product_name, "quantity": item.quantity,
                            "price": round(float(item.price), 2),
                            "total": round(float(item.price * item.quantity), 2)}
                           for item in items]
        return result


@app.get("/api/stats/breakdown")
def get_breakdown(days: int = 30, date_from: date | None = None, date_to: date | None = None):
    since, end, _, _, _ = _date_window(days, date_from, date_to)
    with Session(engine) as db:
        rows = (_contributing(db.query(
            OrderItem.product_name.label("name"),
            func.sum(OrderItem.quantity).label("quantity"),
            func.sum(OrderItem.price * OrderItem.quantity).label("revenue"),
        ).join(Order, Order.id == OrderItem.order_id), since, end)
                .group_by(OrderItem.product_name).order_by(func.sum(OrderItem.price * OrderItem.quantity).desc()).all())
        return [{"name": r.name, "quantity": int(r.quantity), "revenue": round(float(r.revenue), 2)}
                for r in rows]


@app.get("/api/stats/heatmap")
def get_heatmap(days: int = 30):
    since, end, _, _, _ = _date_window(days)
    local_time = func.timezone(settings.timezone, Order.created_at)
    dow = (cast(func.extract("dow", local_time), Integer) + 6) % 7
    hour = cast(func.extract("hour", local_time), Integer)
    with Session(engine) as db:
        rows = (_contributing(db.query(dow.label("dow"), hour.label("hour"),
                                       func.count(Order.id).label("orders")), since, end)
                .group_by(dow, hour).all())
        return {"days": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
                "cells": [{"weekday": int(r.dow), "hour": int(r.hour), "orders": int(r.orders)}
                          for r in rows]}


@app.get("/api/forecast")
def get_forecast():
    since, end, _, _, _ = _date_window(30)
    with Session(engine) as db:
        sales = (_contributing(db.query(Product.opencart_id,
                                        func.sum(OrderItem.quantity).label("sold"))
                               .join(OrderItem, OrderItem.product_name == Product.name)
                               .join(Order, Order.id == OrderItem.order_id), since, end)
                 .group_by(Product.opencart_id).all())
        sold_by_id = {r.opencart_id: int(r.sold) for r in sales}
        products = db.query(Product).filter(Product.is_deleted.is_(False)).all()
        result = []
        for product in products:
            daily = sold_by_id.get(product.opencart_id, 0) / 30
            remaining = (0 if product.quantity <= 0 else
                         round(product.quantity / daily, 1) if daily else None)
            result.append({"id": product.opencart_id, "name": product.name,
                           "quantity": product.quantity, "sold_30d": sold_by_id.get(product.opencart_id, 0),
                           "days_remaining": remaining,
                           "critical": remaining is not None and remaining <= 7})
        return sorted(result, key=lambda r: (r["days_remaining"] is None,
                                             r["days_remaining"] or float("inf")))


@app.get("/api/analytics/abc")
def get_abc(days: int = 90, date_from: date | None = None, date_to: date | None = None):
    since, end, _, _, _ = _date_window(days, date_from, date_to)
    with Session(engine) as db:
        rows = (_contributing(db.query(OrderItem.product_name.label("name"),
                                       func.sum(OrderItem.price * OrderItem.quantity).label("revenue"))
                              .join(Order, Order.id == OrderItem.order_id), since, end)
                .group_by(OrderItem.product_name).order_by(func.sum(OrderItem.price * OrderItem.quantity).desc()).all())
        total = sum((r.revenue for r in rows), 0)
        cumulative = 0
        result = []
        for row in rows:
            share = float(row.revenue / total) if total else 0
            cumulative += share
            bucket = "A" if cumulative - share < 0.8 else ("B" if cumulative - share < 0.95 else "C")
            result.append({"name": row.name, "revenue": round(float(row.revenue), 2),
                           "share": round(share * 100, 2),
                           "cumulative_share": round(cumulative * 100, 2), "class": bucket})
        return result


@app.get("/api/export/orders.csv")
def export_orders(status: str | None = None, q: str | None = None,
                  date_from: date | None = None, date_to: date | None = None,
                  sort: str = "created_at", order: str = "desc"):
    sort_columns = {"id": Order.opencart_id, "created_at": Order.created_at,
                    "total": Order.total, "status": Order.status,
                    "customer": Order.customer_name}
    if sort not in sort_columns or order.lower() not in {"asc", "desc"}:
        raise HTTPException(status_code=422, detail="Недопустимая сортировка")
    with Session(engine) as db:
        query = _order_filter(db.query(Order), status=status, q=q,
                              date_from=date_from, date_to=date_to)
        column = sort_columns[sort]
        rows = query.order_by(column.desc() if order.lower() == "desc" else column.asc()).all()
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["Номер", "Дата", "Клиент", "Статус", "Сумма", "Валюта"])
        for item in rows:
            writer.writerow([item.opencart_id, item.created_at.isoformat(), item.customer_name,
                             item.status, f"{item.total:.2f}", settings.currency])
        return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=orders.csv"})


@app.get("/api/sync/log")
def get_sync_log():
    with Session(engine) as db:
        rows = db.query(SyncLog).order_by(SyncLog.id.desc()).limit(20).all()
        return [{"id": r.id, "synced_at": r.synced_at.isoformat() if r.synced_at else None,
                 "orders_synced": r.orders_synced, "status": r.status,
                 "error": r.error_message} for r in rows]


@app.get("/health")
def health():
    result = {"status": "ok", "postgres": False,
              "opencart": None if settings.demo_mode else False,
              "demo_mode": settings.demo_mode,
              "last_sync": None, "timezone": settings.timezone}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        result["postgres"] = True
    except Exception:
        result["status"] = "degraded"
    if not settings.demo_mode:
        try:
            from opencart import get_opencart_connection
            conn = get_opencart_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                result["opencart"] = True
            finally:
                conn.close()
        except Exception:
            result["status"] = "degraded"
    with Session(engine) as db:
        last = db.query(SyncLog).filter(SyncLog.status == "ok").order_by(SyncLog.id.desc()).first()
        result["last_sync"] = last.synced_at.isoformat() if last and last.synced_at else None
    return result


@app.get("/api/products")
def get_products():
    with Session(engine) as db:
        products = (db.query(Product).filter(Product.is_deleted.is_(False))
                    .order_by(Product.quantity).limit(20).all())
        return [
            {"name": product.name, "quantity": product.quantity,
             "price": round(float(product.price), 2)}
            for product in products
        ]


async def _announce(result: SyncResult) -> None:
    if result.new_count and not result.initial:
        await notify_new_orders(result.new_orders)
        if not result.full:
            delivered = await publish("orders", {"orders": result.new_orders})
            log.info("announced %s new order(s) to %s client(s)", result.new_count, delivered)
    if result.low_stock_products:
        await notify_low_stock(result.low_stock_products)


def _money_text(value) -> str:
    amount = f"{float(value):,.2f}".replace(",", " ").rstrip("0").rstrip(".")
    return f"{amount} ₴"


def _bot_stats_text(stats: dict, title: str) -> str:
    return (f"<b>{title}</b>\n"
            f"Виручка: {_money_text(stats['total_revenue'])}\n"
            f"Замовлення: {stats['orders_count']}\n"
            f"Середній чек: {_money_text(stats['avg_check'])}")


async def _bot_command(command_name: str) -> str:
    if command_name in {"/start", "/help"}:
        return ("<b>Команди магазину</b>\n/stats — за 7 днів\n/today — сьогодні\n"
                "/low — товари з малим залишком\n/top — топ товарів за 30 днів\n"
                "/last — останні 5 замовлень")
    if command_name == "/stats":
        stats = await asyncio.to_thread(get_stats, 7)
        return _bot_stats_text(stats, "За останні 7 днів")
    if command_name == "/today":
        today = datetime.now(shop_zone()).date()
        stats = await asyncio.to_thread(get_stats, 7, today, today)
        return _bot_stats_text(stats, f"Сьогодні, {today:%d.%m.%Y}")
    if command_name == "/low":
        low = await asyncio.to_thread(_get_low_stock_products)
        if not low:
            return "✅ Немає товарів із критично низьким залишком."
        lines = [f"⚠️ <b>Мало на складі (≤{settings.low_stock_threshold})</b>"]
        lines.extend(f"• {escape(row['name'])}: {row['quantity']} шт." for row in low[:20])
        if len(low) > 20:
            lines.append(f"Ще товарів: {len(low) - 20}")
        return "\n".join(lines)
    if command_name == "/top":
        stats = await asyncio.to_thread(get_stats, 30)
        products = stats["top_products"]
        if not products:
            return "За останні 30 днів продажів товарів немає."
        lines = ["<b>Топ товарів за 30 днів</b>"]
        lines.extend(f"{index}. {escape(item['name'])} — {item['qty']} шт."
                     for index, item in enumerate(products, 1))
        return "\n".join(lines)
    if command_name == "/last":
        page = await asyncio.to_thread(get_orders, page=1, page_size=5)
        rows = page["items"]
        if not rows:
            return "Замовлень поки немає."
        lines = ["<b>Останні замовлення</b>"]
        for row in rows:
            created = datetime.fromisoformat(row["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            created = created.astimezone(shop_zone()).strftime("%d.%m %H:%M")
            lines.append(f"#{row['id']} · {created} · {escape(row['status'] or '—')} · "
                         f"{_money_text(row['total'])}")
        return "\n".join(lines)
    return "Не знаю таку команду. Надішли /help для списку команд."


def _reserve_report(key: str) -> bool:
    from sqlalchemy.exc import IntegrityError

    with Session(engine) as db:
        if db.query(BotDelivery).filter_by(delivery_key=key).first():
            return False
        db.add(BotDelivery(delivery_key=key, sent_at=datetime.now(UTC)))
        try:
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            return False


def _release_report(key: str):
    with Session(engine) as db:
        db.query(BotDelivery).filter_by(delivery_key=key).delete()
        db.commit()


async def _morning_report_loop():
    try:
        report_time = day_time.fromisoformat(settings.telegram_morning_report_time)
    except ValueError:
        log.error("invalid TELEGRAM_MORNING_REPORT_TIME; morning report is disabled")
        return
    while True:
        now = datetime.now(shop_zone())
        if now.time().replace(tzinfo=None) >= report_time.replace(tzinfo=None):
            key = f"morning:{now.date().isoformat()}"
            if await asyncio.to_thread(_reserve_report, key):
                try:
                    yesterday = now.date() - timedelta(days=1)
                    stats = await asyncio.to_thread(get_stats, 7, yesterday, yesterday)
                    low_count = len(await asyncio.to_thread(_get_low_stock_products))
                    message = (_bot_stats_text(stats, f"Ранковий звіт за {yesterday:%d.%m.%Y}")
                               + f"\nТоварів із низьким залишком: {low_count}")
                    if not await send_to_allowed(message):
                        await asyncio.to_thread(_release_report, key)
                except Exception:
                    log.exception("morning Telegram report failed")
                    await asyncio.to_thread(_release_report, key)
        await asyncio.sleep(60)


def _get_low_stock_products():
    with Session(engine) as db:
        products = (db.query(Product).filter(
            Product.is_deleted.is_(False),
            Product.quantity <= settings.low_stock_threshold,
        ).order_by(Product.quantity, Product.name).all())
        return [{"name": product.name, "quantity": product.quantity} for product in products]


def create_postgres_demo_order():
    """Создаёт ограниченный тестовый заказ в облачной демо-БД."""
    buyers = ("Андрій Коваленко", "Олена Шевченко", "Максим Бондаренко",
              "Ірина Ткаченко", "Дмитро Мельник", "Софія Лисенко")
    now = datetime.now(shop_zone())
    with Session(engine) as db:
        products = db.query(Product).filter(
            Product.is_deleted.is_(False), Product.quantity > 0, Product.price > 0
        ).all()
        if not products:
            raise RuntimeError("У демо-магазині немає товарів із залишком")
        random.shuffle(products)
        lines, total = [], Decimal("0.00")
        for product in products:
            quantity = random.choices([1, 2], [8, 2])[0]
            line_total = Decimal(product.price) * quantity
            if total + line_total > Decimal("7000"):
                continue
            lines.append((product, quantity))
            total += line_total
            if len(lines) == 3:
                break
        if not lines:
            affordable = [p for p in products if Decimal(p.price) <= Decimal("7000")]
            if not affordable:
                raise RuntimeError("Нет товаров в лимите суммы тестового заказа")
            lines = [(random.choice(affordable), 1)]
            total = Decimal(lines[0][0].price)

        last_id = db.query(func.max(Order.opencart_id)).scalar() or 0
        order = Order(opencart_id=last_id + 1, created_at=now, total=total,
                      status="Processing", customer_name=random.choice(buyers),
                      is_deleted=False)
        db.add(order)
        db.flush()
        for product, quantity in lines:
            db.add(OrderItem(order_id=order.id, product_name=product.name,
                             quantity=quantity, price=product.price))
            product.quantity -= quantity
        db.commit()
        payload = {"id": order.opencart_id, "customer": order.customer_name,
                   "total": float(total), "created_at": now.isoformat(),
                   "status": order.status,
                   "items": [f"{p.name} × {q}" for p, q in lines]}
        return payload | {"_notification": {
            "id": order.opencart_id, "customer": order.customer_name,
            "total": float(total), "created_at": now.isoformat(),
            "status": order.status}}


@app.post("/api/sync")
async def run_sync(_authorized: bool = Depends(
        _require_action_token)):
    if settings.demo_mode:
        raise HTTPException(status_code=409, detail="В демо-режиме синхронизация OpenCart отключена")
    try:
        result = await asyncio.to_thread(sync, True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Sync failed: {str(exc)[:200]}") from exc
    await _announce(result)
    return {"status": "ok", "new_orders": result.new_count, "full": result.full}


@app.get("/api/events")
async def get_events():
    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


@app.get("/api/live")
def get_live():
    return {"subscribers": subscriber_count()}


@app.post("/api/simulate")
async def run_simulate(request: Request):
    enforce_rate_limit(simulate_limiter, request, settings.trusted_proxy_ips)
    try:
        if settings.demo_mode:
            order = await asyncio.to_thread(create_postgres_demo_order)
            notification = order.pop("_notification")
            result = SyncResult(new_orders=[notification])
        else:
            order = await asyncio.to_thread(create_demo_order)
            result = await asyncio.to_thread(sync)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Simulate failed: {str(exc)[:200]}") from exc
    await _announce(result)
    return {"status": "ok", "order": order, "new_orders": result.new_count}


def _safe_sync():
    try:
        return sync()
    except Exception:
        return None


async def _periodic_sync():
    while True:
        await asyncio.sleep(max(1, settings.sync_interval_minutes) * 60)
        result = await asyncio.to_thread(_safe_sync)
        if result is not None:
            await _announce(result)


@app.on_event("startup")
async def startup():
    for attempt in range(30):
        try:
            command.upgrade(Config("alembic.ini"), "head")
            break
        except Exception as exc:
            log.warning("postgres/migrations not ready (%s), retry %s/30",
                        type(exc).__name__, attempt + 1)
            time.sleep(2)
    else:
        raise RuntimeError("Postgres is not reachable")

    if not settings.demo_mode:
        first = await asyncio.to_thread(_safe_sync)
        if first is not None:
            await _announce(first)
        asyncio.create_task(_periodic_sync())
    if settings.telegram_bot_token and settings.telegram_chat_ids:
        asyncio.create_task(run_polling(_bot_command))
        asyncio.create_task(_morning_report_loop())
    elif settings.telegram_bot_token or settings.telegram_allowed_chat_ids:
        log.warning("Telegram configured incompletely; set both token and allowed chat IDs")
