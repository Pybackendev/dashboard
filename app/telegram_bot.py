"""Telegram long-polling client and allowlisted message delivery."""
import asyncio
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import settings

log = logging.getLogger("telegram")
API = "https://api.telegram.org/bot{}/{}"


def _request(method: str, payload: dict):
    url = API.format(settings.telegram_bot_token, method)
    request = Request(url, data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=35) as response:
        result = json.loads(response.read())
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API {method} failed")
    return result["result"]


async def send_message(chat_id: int, text: str):
    return await asyncio.to_thread(_request, "sendMessage", {
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


async def send_to_allowed(text: str):
    if not settings.telegram_bot_token:
        return False
    delivered = 0
    for chat_id in settings.telegram_chat_ids:
        try:
            await send_message(chat_id, text)
            delivered += 1
        except Exception:
            log.exception("could not send Telegram message to allowlisted chat")
    return delivered > 0


async def notify_new_orders(orders: list[dict]):
    from html import escape

    for order in orders:
        customer = escape(order.get("customer") or "Клієнт")
        status = escape(order.get("status") or "—")
        amount = float(order.get("total") or 0)
        await send_to_allowed(
            f"🛍 <b>Нове замовлення #{int(order['id'])}</b>\n"
            f"Клієнт: {customer}\nСума: {amount:,.2f} ₴\nСтатус: {status}"
        )


async def notify_low_stock(products: list[dict]):
    from html import escape

    lines = ["⚠️ <b>Мало товару на складі</b>"]
    lines.extend(f"• {escape(p['name'])}: {p['quantity']} шт." for p in products)
    await send_to_allowed("\n".join(lines))


async def run_polling(command_handler):
    if not settings.telegram_bot_token or not settings.telegram_chat_ids:
        log.info("Telegram bot disabled: token or allowed chat IDs are not configured")
        return

    allowed = set(settings.telegram_chat_ids)
    offset = None
    while True:
        try:
            payload = {"timeout": 25, "allowed_updates": ["message"]}
            if offset is not None:
                payload["offset"] = offset
            updates = await asyncio.to_thread(_request, "getUpdates", payload)
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                if chat.get("id") not in allowed:
                    continue
                command = (message.get("text") or "").strip().split()
                if not command or not command[0].startswith("/"):
                    continue
                name = command[0].split("@", 1)[0].lower()
                try:
                    answer = await command_handler(name)
                    await send_message(chat["id"], answer)
                except Exception:
                    log.exception("Telegram command handling failed")
                    await send_message(chat["id"], "Не вдалося отримати дані. Спробуй пізніше.")
        except (HTTPError, URLError, TimeoutError, OSError, RuntimeError):
            log.exception("Telegram polling failed; retrying shortly")
            await asyncio.sleep(5)
        except Exception:
            log.exception("unexpected Telegram polling error")
            await asyncio.sleep(5)
