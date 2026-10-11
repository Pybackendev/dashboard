import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from config import settings
from models import Order, OrderItem, Product, SyncLog
from opencart import get_opencart_connection

log = logging.getLogger("sync")

engine = create_engine(settings.database_url, pool_pre_ping=True)


@dataclass
class SyncResult:
    """Итог одного прохода синхронизации.

    new_orders нужен, чтобы приложение могло разослать событие о свежих
    заказах в дашборд (SSE), а не только записать счётчик в sync_log.
    """
    new_orders: list = field(default_factory=list)

    @property
    def new_count(self) -> int:
        return len(self.new_orders)


def _read_opencart():
    p = settings.opencart_prefix
    oc = get_opencart_connection()
    try:
        with oc.cursor() as cur:
            cur.execute(f"""
                SELECT o.order_id, o.date_added, o.total,
                       os.name AS status,
                       CONCAT(o.firstname, ' ', o.lastname) AS customer_name
                FROM `{p}order` o
                JOIN `{p}order_status` os ON o.order_status_id = os.order_status_id
                WHERE os.language_id = 1
            """)
            orders = cur.fetchall()

            cur.execute(f"SELECT order_id, name, quantity, price FROM `{p}order_product`")
            items = cur.fetchall()

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


def sync():
    """Один проход синхронизации. Возвращает SyncResult."""
    try:
        orders, items, products = _read_opencart()

        with Session(engine) as db:
            existing = {o.opencart_id: o for o in db.query(Order).all()}
            new_orders = {}

            for o in orders:
                oid = o["order_id"]
                if oid in existing:
                    ex = existing[oid]
                    ex.status = o["status"]
                    ex.total = float(o["total"])
                else:
                    obj = Order(
                        opencart_id=oid,
                        created_at=o["date_added"],
                        total=float(o["total"]),
                        status=o["status"],
                        customer_name=(o["customer_name"] or "").strip()[:100],
                    )
                    db.add(obj)
                    new_orders[oid] = obj

            # Получаем id новых заказов, не полагаясь на autoflush
            db.flush()

            # Позиции — только для новых заказов, иначе были бы дубли при каждом sync
            for i in items:
                parent = new_orders.get(i["order_id"])
                if parent is not None:
                    db.add(OrderItem(
                        order_id=parent.id,
                        product_name=(i["name"] or "")[:200],
                        quantity=int(i["quantity"]),
                        price=float(i["price"]),
                    ))

            prod_existing = {p.opencart_id: p for p in db.query(Product).all()}
            for p in products:
                ex = prod_existing.get(p["product_id"])
                if ex:
                    ex.name = (p["name"] or "")[:200]
                    ex.quantity = int(p["quantity"])
                    ex.price = float(p["price"])
                else:
                    db.add(Product(
                        opencart_id=p["product_id"],
                        name=(p["name"] or "")[:200],
                        quantity=int(p["quantity"]),
                        price=float(p["price"]),
                    ))

            added = [
                {
                    "id": oid,
                    "customer": obj.customer_name or "",
                    "total": obj.total,
                    "created_at": obj.created_at.isoformat() if obj.created_at else None,
                    "status": obj.status,
                }
                for oid, obj in new_orders.items()
            ]

            db.add(SyncLog(synced_at=datetime.utcnow(), orders_synced=len(added), status="ok"))
            db.commit()
        return SyncResult(new_orders=added)

    except Exception as e:
        log.error("sync failed: %s", e)
        traceback.print_exc()
        try:
            with Session(engine) as db:
                db.add(SyncLog(synced_at=datetime.utcnow(), orders_synced=0,
                               status="error", error_message=str(e)[:2000]))
                db.commit()
        except Exception:
            log.exception("could not write sync_log")
        raise
