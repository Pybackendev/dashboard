"""Общие фикстуры. Тесты с базой идут в отдельной БД *_test: они очищают все таблицы."""
import os
import sys
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models import Base  # noqa: E402

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://dashboard:dashboard@localhost:5432/dashboard_test"
)
CREATE_DB_HINT = "docker compose exec postgres createdb -U dashboard dashboard_test"


def _unavailable(reason: str):
    # В CI отсутствие базы должно ронять тесты, а не молча пропускать их.
    if os.environ.get("CI"):
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def pg_engine():
    url = make_url(TEST_DB_URL)
    if not (url.database or "").endswith("_test"):
        _unavailable("TEST_DATABASE_URL должен указывать на базу с именем *_test (тесты очищают таблицы)")
    engine = create_engine(TEST_DB_URL)
    try:
        with engine.connect():
            pass
    except OperationalError:
        engine.dispose()
        _unavailable(f"Тестовый Postgres недоступен. Создай базу: {CREATE_DB_HINT}")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_engine(pg_engine, monkeypatch):
    import main
    import sync

    with pg_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    monkeypatch.setattr(sync, "engine", pg_engine)
    monkeypatch.setattr(main, "engine", pg_engine)
    return pg_engine


class FakeShop:
    """Подмена базы OpenCart: отдаёт то, что лежит в списках, как это сделал бы MariaDB."""

    def __init__(self):
        self.orders, self.items, self.products = [], [], []

    def add_order(self, oid, when, total, status="Complete", customer="Іван Коваленко",
                  items=(("Миша", 1, None),), modified=None):
        self.orders.append({
            "order_id": oid, "date_added": when, "date_modified": modified or when,
            "total": total, "status": status, "customer_name": customer,
        })
        for name, qty, price in items:
            self.items.append({"order_id": oid, "name": name, "quantity": qty,
                               "price": price if price is not None else total})

    def remove_order(self, oid):
        self.orders = [o for o in self.orders if o["order_id"] != oid]
        self.items = [i for i in self.items if i["order_id"] != oid]

    def set_items(self, oid, rows):
        self.items = [i for i in self.items if i["order_id"] != oid]
        for name, qty, price in rows:
            self.items.append({"order_id": oid, "name": name, "quantity": qty, "price": price})

    def add_product(self, pid, name, quantity, price=100):
        self.products.append({"product_id": pid, "name": name, "quantity": quantity, "price": price})

    def connection(self):
        shop = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                if "order_product" in sql:
                    ids = set(params or [])
                    self._rows = [i for i in shop.items if i["order_id"] in ids]
                elif "product_description" in sql:
                    self._rows = list(shop.products)
                else:
                    rows = list(shop.orders)
                    if params and "date_modified >=" in sql:
                        rows = [o for o in rows if o["date_modified"] >= params[0]]
                    self._rows = rows

            def fetchall(self):
                return self._rows

        class Connection:
            def cursor(self):
                return Cursor()

            def close(self):
                pass

        return Connection()


@pytest.fixture
def shop(monkeypatch):
    import sync

    fake = FakeShop()
    monkeypatch.setattr(sync, "get_opencart_connection", fake.connection)
    return fake


def local(y, m, d, h=12, mi=0):
    """Время магазина так, как его хранит OpenCart: без часового пояса."""
    return datetime(y, m, d, h, mi)
