"""Наполняет тестовый OpenCart товарами и заказами за 90 дней.
Запуск (OpenCart уже установлен через веб-установщик, контейнеры подняты):
    pip install pymysql
    python seed_opencart.py            # хост 127.0.0.1:3306
Повторный запуск безопасен: старые демо-данные (model SEED-*, email seed@demo.local) пересоздаются.
Обязательные колонки без дефолта подставляются автоматически по information_schema,
поэтому скрипт работает на разных версиях OpenCart (3.x / 4.x)."""
import os, random, sys
from datetime import datetime, timedelta
import pymysql

HOST = os.getenv("OC_HOST", "127.0.0.1"); PORT = int(os.getenv("OC_PORT", "3306"))
USER = os.getenv("OC_USER", "opencart"); PWD = os.getenv("OC_PASS", "opencart_pass")
DB = os.getenv("OC_DB", "opencart"); P = os.getenv("OC_PREFIX", "oc_")
random.seed(42)

con = pymysql.connect(host=HOST, port=PORT, user=USER, password=PWD, database=DB,
                      charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=False)
cur = con.cursor()

def schema(table):
    cur.execute("""SELECT COLUMN_NAME n, DATA_TYPE t, IS_NULLABLE nl, COLUMN_DEFAULT d, EXTRA e
                   FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s""", (DB, P + table))
    cols = cur.fetchall()
    if not cols:
        sys.exit(f"Таблица {P}{table} не найдена — сначала пройди установку OpenCart на http://localhost:8080")
    return cols

def fill(cols, vals, now):
    out = dict(vals)
    for c in cols:
        n = c["n"]
        if n in out or "auto_increment" in (c["e"] or "") or "GENERATED" in (c["e"] or ""):
            continue
        if c["nl"] == "YES" or c["d"] is not None:
            continue
        t = c["t"]
        if t in ("int", "tinyint", "smallint", "mediumint", "bigint", "decimal", "float", "double"): out[n] = 0
        elif t in ("datetime", "timestamp", "date"): out[n] = now
        elif t == "json": out[n] = "[]"
        else: out[n] = ""
    return out

def insert(table, cols, vals, now):
    v = fill(cols, vals, now)
    keys = list(v)
    cur.execute(f"INSERT INTO `{P}{table}` ({','.join('`'+k+'`' for k in keys)}) VALUES ({','.join(['%s']*len(keys))})",
                [v[k] for k in keys])
    return cur.lastrowid

now = datetime.now().replace(microsecond=0)
S = {t: schema(t) for t in ("product", "product_description", "order", "order_product")}

# --- статусы
cur.execute(f"SELECT order_status_id id, name FROM `{P}order_status` WHERE language_id=1")
st = {r["name"]: r["id"] for r in cur.fetchall()}
if not st: sys.exit("Нет статусов заказов (language_id=1)")
def sid(name): return st.get(name) or next(iter(st.values()))
STATUSES = [("Complete", 62), ("Shipped", 12), ("Processing", 10), ("Pending", 10), ("Canceled", 6)]

# --- чистим прошлый seed
cur.execute(f"SELECT order_id FROM `{P}order` WHERE email='seed@demo.local'")
ids = [r["order_id"] for r in cur.fetchall()]
for i in ids:
    cur.execute(f"DELETE FROM `{P}order_product` WHERE order_id=%s", (i,))
    cur.execute(f"DELETE FROM `{P}order` WHERE order_id=%s", (i,))
cur.execute(f"SELECT product_id FROM `{P}product` WHERE model LIKE 'SEED-%%'")
for r in cur.fetchall():
    for t in ("product_description", "product_to_store", "product"):
        try: cur.execute(f"DELETE FROM `{P}{t}` WHERE product_id=%s", (r["product_id"],))
        except Exception: pass

# --- товары: (название, цена, остаток, вес популярности)
PRODUCTS = [
    ("Беспроводные наушники AirBeat", 89, 34, 10), ("Смарт-часы FitPro 5", 149, 3, 9),
    ("Портативная колонка BoomMini", 59, 21, 8), ("Powerbank 20000 mAh", 39, 4, 8),
    ("Игровая мышь Razor X", 45, 18, 6), ("Механическая клавиатура K87", 99, 2, 5),
    ("Веб-камера 1080p", 49, 27, 4), ("Монитор 27\" IPS", 259, 7, 3),
    ("USB-C хаб 7-в-1", 35, 40, 6), ("Рюкзак для ноутбука", 55, 15, 4),
    ("Зарядка GaN 65W", 29, 5, 7), ("Светодиодная лента RGB", 19, 0, 3),
]
pid = {}
for name, price, qty, _ in PRODUCTS:
    k = len(pid) + 1
    i = insert("product", S["product"], dict(model=f"SEED-{k:02d}", sku=f"SEED-{k:02d}", quantity=qty,
               price=price, status=1, stock_status_id=7, date_added=now, date_modified=now,
               date_available=now.date(), minimum=1, subtract=1), now)
    insert("product_description", S["product_description"],
           dict(product_id=i, language_id=1, name=name, meta_title=name), now)
    try: cur.execute(f"INSERT INTO `{P}product_to_store` (product_id, store_id) VALUES (%s,0)", (i,))
    except Exception: pass
    pid[name] = i

# --- заказы
FIRST = ["Андрій","Олена","Максим","Ірина","Дмитро","Наталія","Богдан","Софія","Олег","Вікторія","Тарас","Марина","Юрій","Анна","Роман"]
LAST = ["Коваленко","Шевченко","Бондаренко","Ткаченко","Мельник","Кравченко","Олійник","Лисенко","Поляков","Савченко"]
names = [n for n, *_ in PRODUCTS]; price_of = {n: p for n, p, *_ in PRODUCTS}
weights = [w for *_, w in PRODUCTS]
status_names = [s for s, _ in STATUSES]; status_w = [w for _, w in STATUSES]
DAYS = 90; total_orders = 0
for d in range(DAYS, -1, -1):
    day = now - timedelta(days=d)
    base = 3 + (DAYS - d) / DAYS * 3            # рост от ~3 до ~6 заказов в день
    if day.weekday() >= 5: base *= 0.7
    for _ in range(max(0, int(random.gauss(base, 1.3)))):
        when = day.replace(hour=random.randint(9, 22), minute=random.randint(0, 59), second=random.randint(0, 59))
        if when > now: continue
        status = random.choices(status_names, status_w)[0]
        if d < 2 and status in ("Complete", "Shipped"): status = random.choice(["Pending", "Processing"])
        lines, seen = [], set()
        for _ in range(random.choices([1, 2, 3], [60, 30, 10])[0]):
            n = random.choices(names, weights)[0]
            if n in seen: continue
            seen.add(n); lines.append((n, random.choices([1, 2, 3], [75, 18, 7])[0]))
        total = sum(price_of[n] * q for n, q in lines)
        oid = insert("order", S["order"], dict(
            store_id=0, store_name="Your Store", firstname=random.choice(FIRST), lastname=random.choice(LAST),
            email="seed@demo.local", telephone="+380000000000", order_status_id=sid(status), language_id=1,
            currency_code="USD", currency_value=1, total=total, date_added=when, date_modified=when), now)
        for n, q in lines:
            insert("order_product", S["order_product"], dict(order_id=oid, product_id=pid[n], name=n,
                   model="SEED", quantity=q, price=price_of[n], total=price_of[n] * q), now)
        total_orders += 1

con.commit()
print(f"Готово: {len(PRODUCTS)} товаров, {total_orders} заказов за {DAYS} дней.")
print("Мало на складе (<=5):", ", ".join(n for n, _, q, _ in PRODUCTS if q <= 5))
