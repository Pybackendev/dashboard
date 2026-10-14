"""Синхронизация и расчёты на настоящем Postgres (подменён только OpenCart)."""
from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

import main
import sync
from conftest import local
from models import Order, OrderItem, Product


def counts(engine):
    with Session(engine) as db:
        return db.query(Order).count(), db.query(OrderItem).count(), db.query(Product).count()


def test_sync_imports_orders_and_stores_utc(db_engine, shop):
    shop.add_order(1, local(2026, 10, 13, 0, 30), 500, items=(("Миша", 2, 250),))
    shop.add_product(1, "Миша", 18, 250)
    result = sync.sync()
    assert result.new_count == 1 and result.initial
    with Session(db_engine) as db:
        order = db.query(Order).one()
        assert order.created_at == datetime(2026, 10, 12, 21, 30, tzinfo=UTC)  # 00:30 Київ
        assert float(order.total) == 500 and order.is_deleted is False
        assert db.query(OrderItem).one().quantity == 2


def test_repeated_syncs_do_not_duplicate_items(db_engine, shop):
    shop.add_order(1, local(2026, 10, 12), 300, items=(("Миша", 1, 100), ("Чохол", 2, 100)))
    shop.add_product(1, "Миша", 18)
    sync.sync()
    first = counts(db_engine)
    assert sync.sync().new_count == 0
    assert sync.sync(full=True).new_count == 0
    assert counts(db_engine) == first == (1, 2, 1)


def test_changed_order_replaces_items_and_status(db_engine, shop):
    shop.add_order(1, local(2026, 10, 12, 10), 300, status="Pending",
                   items=(("Миша", 1, 100), ("Чохол", 2, 100)))
    sync.sync()
    shop.orders[0].update(status="Complete", total=450, date_modified=local(2026, 10, 12, 18))
    shop.set_items(1, [("Миша", 3, 150)])
    sync.sync()                                   # інкрементальний прохід
    with Session(db_engine) as db:
        order = db.query(Order).one()
        assert order.status == "Complete" and float(order.total) == 450
        items = db.query(OrderItem).all()
        assert [(i.product_name, i.quantity, float(i.price)) for i in items] == [("Миша", 3, 150.0)]


def test_full_sync_marks_deleted_and_restores(db_engine, shop):
    shop.add_order(1, local(2026, 10, 12), 100)
    shop.add_order(2, local(2026, 10, 12), 200)
    shop.add_product(1, "Миша", 5)
    sync.sync()
    shop.remove_order(2)
    sync.sync(full=True)
    with Session(db_engine) as db:
        assert {o.opencart_id: o.is_deleted for o in db.query(Order)} == {1: False, 2: True}
    shop.add_order(2, local(2026, 10, 12), 200)
    sync.sync(full=True)
    with Session(db_engine) as db:
        assert db.query(Order).filter(Order.is_deleted.is_(True)).count() == 0


def test_low_stock_is_reported_only_when_crossing_the_threshold(db_engine, shop):
    shop.add_product(1, "Миша", 20)
    sync.sync()
    shop.products[0]["quantity"] = 3
    result = sync.sync()
    assert [p["name"] for p in result.low_stock_products] == ["Миша"]
    assert sync.sync().low_stock_products == []   # вже було низько: повторно не повідомляємо


def test_stats_exclude_canceled_deleted_and_group_by_kyiv_day(db_engine, shop):
    shop.add_order(1, local(2026, 10, 12, 23, 30), 100, items=(("Миша", 1, 100),))
    shop.add_order(2, local(2026, 10, 13, 0, 30), 50, status="Pending", items=(("Чохол", 1, 50),))
    shop.add_order(3, local(2026, 10, 13, 10), 999, status="Canceled", items=(("Монітор", 1, 999),))
    shop.add_order(4, local(2026, 10, 13, 11), 777, items=(("Монітор", 1, 777),))
    sync.sync()
    shop.remove_order(4)
    sync.sync(full=True)                          # замовлення 4 видалено з магазину
    stats = main.get_stats(days=7, date_from=date(2026, 10, 12), date_to=date(2026, 10, 13))
    assert stats["orders_count"] == 2 and stats["total_revenue"] == 150
    assert stats["avg_check"] == 75
    assert stats["revenue_by_day"] == {"2026-10-12": 100.0, "2026-10-13": 50.0}
    assert {p["name"] for p in stats["top_products"]} == {"Миша", "Чохол"}


def seed_many(shop, count=25):
    for i in range(1, count + 1):
        shop.add_order(i, local(2026, 10, 1 + (i % 10), 10, i), 100 + i,
                       status="Canceled" if i % 5 == 0 else "Complete",
                       customer="Тарас Мороз" if i % 2 else "Олена Шевченко")
    shop.add_product(1, "Миша", 5)
    sync.sync()


def test_orders_pagination_filters_and_sorting(db_engine, shop):
    seed_many(shop)
    page3 = main.get_orders(page=3, page_size=10)
    assert page3["total"] == 25 and len(page3["items"]) == 5
    assert len(main.get_orders(page=1, page_size=10)["items"]) == 10
    only_canceled = main.get_orders(status="canceled")        # без урахування регістру
    assert only_canceled["total"] == 5
    assert main.get_orders(q="Олена")["total"] == 12
    by_total = main.get_orders(sort="total", order="asc", page_size=3)["items"]
    assert [o["total"] for o in by_total] == sorted(o["total"] for o in by_total)
    with pytest.raises(HTTPException) as err:
        main.get_orders(sort="password; drop table orders")
    assert err.value.status_code == 422


def test_page_size_is_capped(db_engine, shop):
    seed_many(shop)
    assert main.get_orders(page_size=100000)["page_size"] == 100


def test_order_detail_and_not_found(db_engine, shop):
    shop.add_order(7, local(2026, 10, 12), 300, items=(("Миша", 2, 100), ("Чохол", 1, 100)))
    sync.sync()
    detail = main.get_order(7)
    assert detail["id"] == 7 and len(detail["items"]) == 2
    assert {i["total"] for i in detail["items"]} == {200.0, 100.0}
    with pytest.raises(HTTPException) as err:
        main.get_order(999)
    assert err.value.status_code == 404


def test_csv_export_neutralizes_formulas_from_customers(db_engine, shop):
    shop.add_order(1, local(2026, 10, 12), 100, customer='=HYPERLINK("http://evil","click")')
    shop.add_order(2, local(2026, 10, 12), 100, customer="Звичайний Клієнт")
    sync.sync()
    response = main.export_orders(status=None, q=None, date_from=None, date_to=None,
                                  sort="id", order="asc")
    body = response.body.decode("utf-8-sig")
    assert "'=HYPERLINK" in body and ",=HYPERLINK" not in body
    assert "Звичайний Клієнт" in body
