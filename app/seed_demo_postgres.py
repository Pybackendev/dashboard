"""Безопасно заполняет пустой облачный Postgres портфолио-данными.

Запуск из папки app: python seed_demo_postgres.py
Строка подключения вводится скрыто и не сохраняется в файлы.
Скрипт откажется запускаться, если в базе уже есть заказы или товары.
"""
import getpass
import os
import random
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

database_url = getpass.getpass("Neon DATABASE_URL (ввод скрыт): ").strip()
if not database_url:
    raise SystemExit("Строка подключения не указана")
os.environ["DATABASE_URL"] = database_url
os.environ["DEMO_MODE"] = "true"

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from config import settings  # noqa: E402
from models import DailySnapshot, Order, OrderItem, Product, SyncLog  # noqa: E402

random.seed(42)
zone = ZoneInfo(settings.timezone)
products_seed = [
    ("Бездротові навушники AirBeat", 1899, 34, 10),
    ("Смарт-годинник FitPro 5", 3299, 3, 9),
    ("Портативна колонка BoomMini", 1299, 21, 8),
    ("Павербанк 20 000 мА·год", 999, 4, 8),
    ("Ігрова миша Razor X", 1599, 18, 6),
    ("Механічна клавіатура K87", 2499, 2, 5),
    ("Вебкамера 1080p", 1799, 27, 4),
    ("Монітор 27\" IPS", 8999, 7, 3),
    ("USB-C хаб 7-в-1", 1199, 40, 6),
    ("Рюкзак для ноутбука", 1899, 15, 4),
    ("Зарядний пристрій GaN 65 Вт", 1099, 5, 7),
    ("Світлодіодна стрічка RGB", 599, 0, 3),
]
first_names = ["Андрій", "Олена", "Максим", "Ірина", "Дмитро", "Наталія", "Богдан", "Софія", "Олег", "Вікторія", "Тарас", "Марина", "Юрій", "Анна", "Роман"]
last_names = ["Коваленко", "Шевченко", "Бондаренко", "Ткаченко", "Мельник", "Кравченко", "Олійник", "Лисенко", "Поляков", "Савченко"]
status_weights = [("Complete", 62), ("Shipped", 12), ("Processing", 10), ("Pending", 10), ("Canceled", 6)]

alembic_cfg = Config("alembic.ini")
command.upgrade(alembic_cfg, "head")
engine = create_engine(settings.database_url, pool_pre_ping=True)
now = datetime.now(zone).replace(microsecond=0)

with Session(engine) as db:
    if db.query(Order).count() or db.query(Product).count():
        raise SystemExit("База вже містить замовлення або товари; seed зупинено без змін")
    products = []
    for idx, (name, price, stock, weight) in enumerate(products_seed, 1):
        product = Product(opencart_id=idx, name=name, quantity=stock,
                          price=Decimal(price), is_deleted=False)
        db.add(product)
        products.append((product, weight))
    db.flush()

    statuses, status_chances = zip(*status_weights)
    order_id = 1000
    for days_ago in range(90, -1, -1):
        day = now - timedelta(days=days_ago)
        avg = 3 + (90 - days_ago) / 90 * 3
        if day.weekday() >= 5:
            avg *= 0.7
        count = max(0, int(random.gauss(avg, 1.3)))
        for _ in range(count):
            created = day.replace(hour=random.randint(9, 22), minute=random.randint(0, 59), second=random.randint(0, 59))
            if created > now:
                continue
            status = random.choices(statuses, status_chances)[0]
            if days_ago < 2 and status in {"Complete", "Shipped"}:
                status = random.choice(["Pending", "Processing"])
            chosen = random.choices(products, weights=[row[1] for row in products], k=3)
            selected = []
            seen = set()
            for product, _weight in chosen:
                if product.id in seen:
                    continue
                seen.add(product.id)
                qty = random.choices([1, 2, 3], [75, 18, 7])[0]
                selected.append((product, qty))
            total = sum((Decimal(product.price) * qty for product, qty in selected), Decimal("0.00"))
            order = Order(opencart_id=order_id, created_at=created, total=total,
                          status=status, customer_name=f"{random.choice(first_names)} {random.choice(last_names)}",
                          is_deleted=False)
            db.add(order)
            db.flush()
            for product, qty in selected:
                db.add(OrderItem(order_id=order.id, product_name=product.name,
                                 quantity=qty, price=product.price))
            order_id += 1

    today = now.date()
    db.merge(DailySnapshot(snapshot_date=today, captured_at=now.astimezone(ZoneInfo("UTC")),
                           low_stock_count=sum(1 for p, _ in products if p.quantity <= settings.low_stock_threshold),
                           product_count=len(products)))
    db.add(SyncLog(synced_at=now.astimezone(ZoneInfo("UTC")), orders_synced=0, status="ok"))
    db.commit()
    print(f"Добавлено: {len(products)} товаров и {order_id - 1000} заказов. Строка подключения не сохранялась.")

engine.dispose()
