import asyncio

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

import main
from security import ActionRateLimiter, client_ip, validate_action_token


def api_post(path: str, headers: dict | None = None):
    async def send():
        transport = httpx.ASGITransport(app=main.app, client=("198.51.100.10", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(path, headers=headers)

    return asyncio.run(send())


def request_from(remote: str, forwarded: str | None = None) -> Request:
    headers = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode("ascii")))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/", "raw_path": b"/",
        "query_string": b"", "headers": headers, "client": (remote, 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_simulation_cooldown_and_hourly_limit():
    limiter = ActionRateLimiter(cooldown_seconds=10, limit_per_hour=3)
    assert limiter.check("198.51.100.1", now=1000) == (True, 0)
    allowed, retry = limiter.check("198.51.100.1", now=1005)
    assert not allowed and retry == 5
    assert limiter.check("198.51.100.1", now=1010) == (True, 0)
    assert limiter.check("198.51.100.1", now=1020) == (True, 0)

    allowed, retry = limiter.check("198.51.100.1", now=1030)
    assert not allowed and retry == 3570
    assert limiter.check("198.51.100.1", now=4620) == (True, 0)


def test_simulation_limits_are_independent_per_ip():
    limiter = ActionRateLimiter(cooldown_seconds=10, limit_per_hour=1)
    assert limiter.check("198.51.100.1", now=10) == (True, 0)
    assert limiter.check("198.51.100.2", now=10) == (True, 0)


def test_forwarded_address_is_ignored_unless_proxy_is_trusted():
    request = request_from("10.0.0.2", "203.0.113.50")
    assert client_ip(request) == "10.0.0.2"
    assert client_ip(request, "10.0.0.0/8") == "203.0.113.50"


def test_forwarded_chain_uses_nearest_untrusted_client():
    request = request_from("10.0.0.2", "192.0.2.10, 10.0.0.3")
    assert client_ip(request, "10.0.0.0/8") == "192.0.2.10"


def test_action_token_fails_closed_when_unconfigured():
    with pytest.raises(HTTPException) as error:
        validate_action_token("anything", "")
    assert error.value.status_code == 503


def test_action_token_rejects_wrong_value_and_accepts_match():
    with pytest.raises(HTTPException) as error:
        validate_action_token("wrong", "secret-token")
    assert error.value.status_code == 401
    assert validate_action_token("secret-token", "secret-token") is True


def test_full_sync_endpoint_rejects_missing_and_wrong_token(monkeypatch):
    monkeypatch.setattr(main.settings, "demo_action_token", "expected-secret")
    assert api_post("/api/sync").status_code == 401
    assert api_post("/api/sync", {"X-Action-Token": "wrong"}).status_code == 401


def test_full_sync_endpoint_fails_closed_without_server_token(monkeypatch):
    monkeypatch.setattr(main.settings, "demo_action_token", "")
    response = api_post("/api/sync")
    assert response.status_code == 503


def test_simulate_endpoint_rejects_when_cooldown_is_active(monkeypatch):
    limiter = ActionRateLimiter(cooldown_seconds=10, limit_per_hour=6)
    monkeypatch.setattr(main, "simulate_limiter", limiter)
    assert limiter.check("198.51.100.10")[0]
    response = api_post("/api/simulate")
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
