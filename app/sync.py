import logging
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from config import settings
from models import DailySnapshot, Order, OrderItem, Product, SyncLog, SyncState
from opencart import get_opencart_connection
from time_utils import source_to_utc, utc_to_source

log = logging.getLogger("sync")
engine = create_engine(settings.database_url, pool_pre_ping=True)
CENT = Decimal("0.01")


@dataclass
class SyncResult:
    new_orders: list = field(default_factory=list)
    low_stock_products: list = field(default_factory=list)
    full: bool = False
    initial: bool = False

    @property
    def new_count(self) -> int:
        return len(self.new_orders)


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def _read_opencart(since: datetime | None = None):
    p = settings.opencart_prefix
    oc = get_opencart_connection()
    try:
        with oc.cursor() as cur:
            query = f"""
                SELECT o.order_id, o.date_added, o.date_modified, o.total,
                       os.name AS status,
                       CONCAT(o.firstname, ' ', o.lastname) AS customer_name
                FROM `{p}order` o
                LEFT JOIN `{p}order_status` os
                  ON o.order_status_id = os.order_status_id AND os.language_id = 1
                WHERE 1 = 1
            """
            if since is not None:
                query += " AND o.date_modified >= %s"
                cur.execute(query, (since,))
            else:
                cur.execute(query)
            orders = cur.fetchall()
            ids = [order["order_id"] for order in orders]

            if ids:
                placeholders = ",".join(["%s"] * len(ids))
                cur.execute(
                    f"SELECT order_id, name, quantity, price FROM `{p}order_product` "
                    f"WHERE order_id IN ({placeholders})",
                    ids,
                )
                items = cur.fetchall()
            else:
                items = []

            cur.execute(f"""
                SELECT pr.product_id, pd.name, pr.quantity, pr.price
                FROM `{p}product` pr
                JOIN `{p}product_description` pd ON pr.product_id = pd.product_id
                WHERE pd.language_id = 1
            """)
            products = cur.fetchall()
        return orders, items, products
    finally:
        oc.close()


def sync(full: bool = False):
    """Incremental sync; full sync also reconciles deleted OpenCart orders."""
    try:
        with Session(engine) as db:
            state = db.get(SyncState, 1)
            now = datetime.now(UTC)
            initial = state is None
            if state is None:
                state = SyncState(id=1)
                db.add(state)
                db.flush()
                full = True
            if state.last_full_sync_at is None:
                full = True
            elif not full:
                age = now - state.last_full_sync_at
                full = age >= timedelta(hours=max(1, settings.full_sync_interval_hours))
            since = None if full else utc_to_source(state.last_order_modified)
            # A fresh installation creates this row here; the processing session
            # below needs to be able to read it independently.
            db.commit()

        orders, items, products = _read_opencart(since=since)
        items_by_order: dict[int, list[dict]] = {}
        for item in items:
            items_by_order.setdefault(item["order_id"], []).append(item)

        with Session(engine) as db:
            state = db.get(SyncState, 1)
            existing_rows = db.query(Order).all() if full else []
            existing = {o.opencart_id: o for o in existing_rows}
            if not full:
                ids = [o["order_id"] for o in orders]
                if ids:
                    existing = {
                        o.opencart_id: o for o in
                        db.query(Order).filter(Order.opencart_id.in_(ids)).all()
                    }

            new_orders = {}
            max_modified = state.last_order_modified
            for row in orders:
                oid = row["order_id"]
                modified_at = source_to_utc(row.get("date_modified") or row["date_added"])
                max_modified = max(filter(None, (max_modified, modified_at)), default=None)
                order = existing.get(oid)
                if order is None:
                    order = Order(
                        opencart_id=oid,
                        created_at=source_to_utc(row["date_added"]),
                        source_modified_at=modified_at,
                        total=_money(row["total"]),
                        status=row["status"],
                        customer_name=(row["customer_name"] or "").strip()[:100],
                        is_deleted=False,
                    )
                    db.add(order)
                    new_orders[oid] = order
                else:
                    order.source_modified_at = modified_at
                    order.total = _money(row["total"])
                    order.status = row["status"]
                    order.customer_name = (row["customer_name"] or "").strip()[:100]
                    order.is_deleted = False
                    db.query(OrderItem).filter(OrderItem.order_id == order.id).delete()

            db.flush()
            for oid, row_items in items_by_order.items():
                order = new_orders.get(oid) or existing.get(oid)
                if order is None:
                    continue
                for item in row_items:
                    db.add(OrderItem(
                        order_id=order.id,
                        product_name=(item["name"] or "")[:200],
                        quantity=int(item["quantity"]),
                        price=_money(item["price"]),
                    ))

            if full:
                seen_ids = {row["order_id"] for row in orders}
                for order in existing_rows:
                    if order.opencart_id not in seen_ids:
                        order.is_deleted = True

            prod_rows = db.query(Product).all()
            prod_existing = {p.opencart_id: p for p in prod_rows}
            seen_product_ids = set()
            low_stock_products = []
            for row in products:
                seen_product_ids.add(row["product_id"])
                product = prod_existing.get(row["product_id"])
                if product:
                    quantity = int(row["quantity"])
                    if (not product.is_deleted
                            and int(product.quantity or 0) > settings.low_stock_threshold
                            and quantity <= settings.low_stock_threshold):
                        low_stock_products.append({
                            "name": (row["name"] or "")[:200], "quantity": quantity,
                        })
                    product.name = (row["name"] or "")[:200]
                    product.quantity = quantity
                    product.price = _money(row["price"])
                    product.is_deleted = False
                else:
                    db.add(Product(
                        opencart_id=row["product_id"],
                        name=(row["name"] or "")[:200],
                        quantity=int(row["quantity"]),
                        price=_money(row["price"]),
                        is_deleted=False,
                    ))
            if full:
                for product in prod_rows:
                    if product.opencart_id not in seen_product_ids:
                        product.is_deleted = True

            snapshot_date = datetime.now(ZoneInfo(settings.timezone)).date()
            active_products = db.query(Product).filter(Product.is_deleted.is_(False))
            snapshot = db.get(DailySnapshot, snapshot_date)
            values = {
                "captured_at": datetime.now(UTC),
                "low_stock_count": active_products.filter(
                    Product.quantity <= settings.low_stock_threshold).count(),
                "product_count": active_products.count(),
            }
            if snapshot is None:
                db.add(DailySnapshot(snapshot_date=snapshot_date, **values))
            else:
                for key, value in values.items():
                    setattr(snapshot, key, value)

            added = [
                {
                    "id": oid,
                    "customer": order.customer_name or "",
                    "total": float(order.total),
                    "created_at": order.created_at.isoformat() if order.created_at else None,
                    "status": order.status,
                }
                for oid, order in new_orders.items()
            ]
            state.last_order_modified = max_modified
            if full:
                state.last_full_sync_at = now
            db.add(SyncLog(synced_at=now, orders_synced=len(added), status="ok"))
            db.commit()
        log.info("sync complete: %s orders, full=%s", len(orders), full)
        return SyncResult(new_orders=added, low_stock_products=low_stock_products,
                          full=full, initial=initial)

    except Exception as exc:
        log.exception("sync failed: %s", exc)
        try:
            with Session(engine) as db:
                db.add(SyncLog(
                    synced_at=datetime.now(UTC), orders_synced=0,
                    status="error", error_message=str(exc)[:2000],
                ))
                db.commit()
        except Exception:
            log.exception("could not write sync_log")
        raise
