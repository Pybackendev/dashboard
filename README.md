# Дашборд интернет-магазина

OpenCart (MariaDB) → синхронизация → PostgreSQL → FastAPI → дашборд на Chart.js (тёмная тема, без React).

## Что показывает
Выручка, заказы и средний чек с дельтой к прошлому периоду (7/30/90 дней), график выручки, топ-5 товаров,
последние заказы, остатки (мало на складе ≤5), кнопка «Синхронизировать», автосинхронизация раз в 5 минут.

## Запуск с нуля
```
docker compose down -v                      # (только если уже запускала раньше: чистим старые тома)
docker compose up -d opencart-db opencart
```
1. Открой http://localhost:8080 и пройди установщик OpenCart:
   хост БД `opencart-db`, порт `3306`, пользователь `opencart`, пароль `opencart_pass`, база `opencart`, префикс `oc_`.
2. Наполни магазин тестовыми данными:
   ```
   pip install pymysql
   python seed_opencart.py
   ```
3. Подними дашборд: `docker compose up -d --build app` и открой http://localhost:8000

Если приложение запущено раньше установки OpenCart, оно всё равно стартует; нажми «Синхронизировать» после seed.

## Структура
- `app/` — FastAPI (`main.py`), синхронизация (`sync.py`), модели, конфиг через переменные окружения (`.env`)
- `frontend/index.html` — дашборд
- `seed_opencart.py` — тестовые товары и заказы за 90 дней
- `docker-compose.prod.yml` — деплой без тестового OpenCart (подключение к реальной БД магазина через `.env`)

## API
`GET /api/stats?days=30`, `GET /api/orders?limit=10`, `GET /api/products`, `POST /api/sync`
