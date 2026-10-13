"""Small in-process guards for public demo actions."""
import ipaddress
import math
import secrets
import threading
import time
from collections import deque

from fastapi import HTTPException, Request


def validate_action_token(supplied: str | None, configured: str) -> bool:
    if not configured:
        raise HTTPException(status_code=503, detail="Действие администратора не настроено")
    if not supplied or not secrets.compare_digest(
        supplied.encode("utf-8"), configured.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Неверный токен действия")
    return True


def _networks(value: str):
    networks = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            # A typo must not make a forwarded client address trusted.
            continue
    return networks


def client_ip(request: Request, trusted_proxy_ips: str = "") -> str:
    remote = request.client.host if request.client else "unknown"
    try:
        remote_address = ipaddress.ip_address(remote)
    except ValueError:
        return remote
    networks = _networks(trusted_proxy_ips)
    if not any(remote_address in network for network in networks):
        return str(remote_address)

    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded:
        return str(remote_address)
    try:
        chain = [ipaddress.ip_address(value.strip()) for value in forwarded.split(",")]
    except ValueError:
        return str(remote_address)
    chain.append(remote_address)
    for address in reversed(chain):
        if not any(address in network for network in networks):
            return str(address)
    return str(chain[0])


class ActionRateLimiter:
    def __init__(self, cooldown_seconds: int = 10, limit_per_hour: int = 6):
        self.cooldown_seconds = max(1, cooldown_seconds)
        self.limit_per_hour = max(1, limit_per_hour)
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> tuple[bool, int]:
        current = time.monotonic() if now is None else now
        hour_ago = current - 3600
        with self._lock:
            stale_keys = []
            for ip, events in self._events.items():
                while events and events[0] <= hour_ago:
                    events.popleft()
                if not events:
                    stale_keys.append(ip)
            for ip in stale_keys:
                del self._events[ip]

            events = self._events.setdefault(key, deque())
            if len(events) >= self.limit_per_hour:
                retry = max(1, math.ceil(3600 - (current - events[0])))
                return False, retry
            if events and current - events[-1] < self.cooldown_seconds:
                retry = max(1, math.ceil(self.cooldown_seconds - (current - events[-1])))
                return False, retry
            events.append(current)
            return True, 0


def enforce_action_token(request: Request, configured: str) -> bool:
    return validate_action_token(request.headers.get("x-action-token"), configured)


def enforce_rate_limit(limiter: ActionRateLimiter, request: Request,
                       trusted_proxy_ips: str = "") -> None:
    allowed, retry_after = limiter.check(client_ip(request, trusted_proxy_ips))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Ліміт тестових замовлень вичерпано. Спробуйте пізніше.",
            headers={"Retry-After": str(retry_after)},
        )
