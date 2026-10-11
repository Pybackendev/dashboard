"""Демо-заказ в OpenCart для кнопки «Симулировать заказ».

Заказ создаётся в реальной базе магазина, как будто его оформил покупатель,
и дальше идёт обычным путём: синхронизация → Postgres → SSE → дашборд.
В базе дашборда ничего не подрисовывается напрямую.

Набор колонок в oc_order отличается от версии к версии, поэтому обязательные
колонки без значения по умолчанию определяются по information_schema — тот же
приём, что и в seed_opencart.py.
"""
import logging
import random
from datetime import datetime

from config import settings
from opencart import get_opencart_connection

log = logging.getLogger("simulate")

BUYERS = (
    ("Андрій", "Коваленко"), ("Олена", "Шевченко"), ("Максим", "Бондаренко"),
    ("Ірина", "Ткаченко"), ("Дмитро", "Мельник"), ("Наталія", "Кравченко"),
    ("Богдан", "Олійник"), ("Софія", "Лисенко"), ("Олег", "Поляков"),
    ("Вікторія", "Савченко"), ("Тарас", "Мороз"), ("Анна", "Гончар"),
)

DEMO_EMAIL = "simulate@demo.local"
DEMO_PHONE = "+380441234567"
CURRENCY_CODE = "USD"
STORE_ID = 0
STORE_NAME = "Your Store"
LANGUAGE_ID = 1

MAX_LINE_ITEMS = 3
QUANTITY_CHOICES = (1, 1, 1, 1, 2, 2, 3)
PREFERRED_STATUS = "Processing"

ADDRESS = {
    "address_1": "вул. Хрещатик, 1",
    "city": "Київ",
    "postcode": "01001",
    "country": "Ukraine",
    "zone": "Київська область",
}

NUMERIC_TYPES = {"int", "tinyint", "smallint", "mediumint", "bigint",
                 "decimal", "float", "double"}
DATE_TYPES = {"datetime", "timestamp", "date"}


def _required_columns(cur, table):
    """NOT NULL колонки без значения по умолчанию: их обязан заполнить INSERT."""
    cur.execute(
        """SELECT COLUMN_NAME AS name, DATA_TYPE AS type
           FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
             AND IS_NULLABLE = 'NO' AND COLUMN_DEFAULT IS NULL
             AND EXTRA NOT LIKE '%%auto_increment%%'
             AND EXTRA NOT LIKE '%%GENERATED%%'""",
        (settings.opencart_db, settings.opencart_prefix + table),
    )
    return cur.fetchall()


def _fallback_value(column_type, now):
    if column_type in NUMERIC_TYPES:
        return 0
    if column_type in DATE_TYPES:
        return now
    if column_type == "json":
        return "[]"
    return ""


def _insert(cur, table, required, values, now):
    row = dict(values)
    for column in required:
        if column["name"] not in row:
            row[column["name"]] = _fallback_value(column["type"], now)
    names = list(row)
    placeholders = ", ".join(["%s"] * len(names))
    cur.execute(
        f"INSERT INTO `{settings.opencart_prefix}{table}` "
        f"({', '.join('`' + name + '`' for name in names)}) VALUES ({placeholders})",
        [row[name] for name in names],
    )
    return cur.lastrowid


def _resolve_status(cur):
    """Статус нового заказа: «Processing», иначе любой доступный."""
    p = settings.opencart_prefix
    cur.execute(
        f"SELECT order_status_id, name FROM `{p}order_status` "
        f"WHERE language_id = %s AND name = %s",
        (LANGUAGE_ID, PREFERRED_STATUS),
    )
    row = cur.fetchone()
    if row is None:
        cur.execute(
            f"SELECT order_status_id, name FROM `{p}order_status` "
            f"WHERE language_id = %s ORDER BY order_status_id LIMIT 1",
            (LANGUAGE_ID,),
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("в OpenCart нет статусов заказов — установка не завершена")
    return row["order_status_id"], row["name"]


def _pick_line_items(cur):
    """Случайные товары для заказа: в приоритете те, что есть на складе."""
    p = settings.opencart_prefix
    cur.execute(
        f"""SELECT pr.product_id, pd.name, pr.price, pr.quantity, pr.model
            FROM `{p}product` pr
            JOIN `{p}product_description` pd
              ON pd.product_id = pr.product_id AND pd.language_id = %s
            WHERE pr.status = 1 AND pr.price > 0""",
        (LANGUAGE_ID,),
    )
    products = cur.fetchall()
    if not products:
        raise RuntimeError("в магазине нет активных товаров с ценой")

    in_stock = [product for product in products if int(product["quantity"]) > 0]
    pool = in_stock or products
    picked = random.sample(pool, min(MAX_LINE_ITEMS, len(pool)))

    return [
        {
            "product_id": product["product_id"],
            "name": (product["name"] or "")[:200],
            "model": (product["model"] or "")[:64],
            "quantity": random.choice(QUANTITY_CHOICES),
            "price": float(product["price"]),
        }
        for product in picked
    ]


def _party(prefix, firstname, lastname, method_key, method, code_key, code):
    values = {f"{prefix}{key}": value for key, value in ADDRESS.items()}
    values[f"{prefix}firstname"] = firstname
    values[f"{prefix}lastname"] = lastname
    values[method_key] = method
    values[code_key] = code
    return values


def create_demo_order():
    """Создаёт заказ в OpenCart и списывает остатки. Возвращает описание заказа."""
    now = datetime.now().replace(microsecond=0)
    firstname, lastname = random.choice(BUYERS)

    connection = get_opencart_connection()
    try:
        with connection.cursor() as cur:
            status_id, status_name = _resolve_status(cur)
            lines = _pick_line_items(cur)
            total = round(sum(line["price"] * line["quantity"] for line in lines), 2)

            order_columns = _required_columns(cur, "order")
            item_columns = _required_columns(cur, "order_product")

            order_values = {
                "store_id": STORE_ID,
                "store_name": STORE_NAME,
                "firstname": firstname,
                "lastname": lastname,
                "email": DEMO_EMAIL,
                "telephone": DEMO_PHONE,
                "language_id": LANGUAGE_ID,
                "currency_code": CURRENCY_CODE,
                "currency_value": 1,
                "order_status_id": status_id,
                "total": total,
                "date_added": now,
                "date_modified": now,
                **_party("payment_", firstname, lastname,
                         "payment_method", "Cash On Delivery", "payment_code", "cod"),
                **_party("shipping_", firstname, lastname,
                         "shipping_method", "Flat Rate", "shipping_code", "flat.flat"),
            }
            order_id = _insert(cur, "order", order_columns, order_values, now)

            for line in lines:
                _insert(cur, "order_product", item_columns, {
                    "order_id": order_id,
                    "product_id": line["product_id"],
                    "name": line["name"],
                    "model": line["model"],
                    "quantity": line["quantity"],
                    "price": line["price"],
                    "total": round(line["price"] * line["quantity"], 2),
                }, now)
                cur.execute(
                    f"UPDATE `{settings.opencart_prefix}product` "
                    f"SET quantity = GREATEST(quantity - %s, 0) "
                    f"WHERE product_id = %s AND subtract = 1",
                    (line["quantity"], line["product_id"]),
                )
        connection.commit()
    finally:
        connection.close()

    log.info("demo order #%s: %s items, total %s", order_id, len(lines), total)
    return {
        "id": order_id,
        "customer": f"{firstname} {lastname}",
        "total": total,
        "created_at": now.isoformat(),
        "status": status_name,
        "items": [f"{line['name']} × {line['quantity']}" for line in lines],
    }
