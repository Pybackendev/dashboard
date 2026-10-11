"""Подключение к базе магазина OpenCart (MariaDB)."""
import pymysql

from config import settings


def get_opencart_connection():
    """Новое соединение с БД магазина. Читать из неё можно всем,
    писать — только демо-генератору заказов (simulate.py)."""
    return pymysql.connect(
        host=settings.opencart_host,
        port=settings.opencart_port,
        user=settings.opencart_user,
        password=settings.opencart_password,
        database=settings.opencart_db,
        charset="utf8mb4",
        connect_timeout=10,
        cursorclass=pymysql.cursors.DictCursor,
    )
