"""Шина событий для живого обновления дашборда (Server-Sent Events).

Публикация идёт только из потока event loop: эндпоинты объявлены как async,
а блокирующая работа уходит в asyncio.to_thread. Поэтому очередь каждого
подписчика можно наполнять напрямую, без локов.
"""
import asyncio
import json
import logging

log = logging.getLogger("events")

# Максимум сообщений в очереди одного клиента: если браузер не читает поток,
# его сообщения теряются, а не копятся в памяти сервера.
QUEUE_SIZE = 100

# Пустой комментарий раз в 20 секунд, чтобы прокси и браузер не закрыли поток.
KEEPALIVE_SECONDS = 20.0

_subscribers = set()


def subscriber_count() -> int:
    return len(_subscribers)


async def publish(event: str, payload: dict) -> int:
    """Рассылает событие всем подключённым клиентам, возвращает число доставок."""
    message = (event, payload)
    delivered = 0
    for queue in list(_subscribers):
        try:
            queue.put_nowait(message)
            delivered += 1
        except asyncio.QueueFull:
            log.warning("SSE client is not reading, event %r dropped", event)
    return delivered


def _frame(event: str, payload: dict) -> str:
    # json.dumps экранирует переводы строк, поэтому формат SSE не ломается.
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def event_stream():
    """Бесконечный поток SSE для одного клиента."""
    queue = asyncio.Queue(maxsize=QUEUE_SIZE)
    _subscribers.add(queue)
    log.info("SSE client connected (%s online)", len(_subscribers))
    try:
        yield _frame("hello", {"subscribers": len(_subscribers)})
        while True:
            try:
                event, payload = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield _frame(event, payload)
    finally:
        _subscribers.discard(queue)
        log.info("SSE client disconnected (%s online)", len(_subscribers))
