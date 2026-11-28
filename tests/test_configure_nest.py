"""Tests for POST /api/configure-nest api_key handling."""

from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from nolongerevil.routes.control import scan


async def _start_device(device_key: str) -> tuple[TestServer, list[dict[str, Any]]]:
    """Start a fake thermostat settings CGI that checks the key case-sensitively."""
    received: list[dict[str, Any]] = []

    async def settings_cgi(request: web.Request) -> web.Response:
        body = await request.json()
        received.append(body)
        if body.get("api_key") != device_key:
            return web.json_response({"status": "unauthorized"}, status=401)
        return web.json_response({"device_name": "Hallway"})

    app = web.Application()
    app.router.add_post("/cgi-bin/api/settings", settings_cgi)
    server = TestServer(app)
    await server.start_server()
    return server, received


@pytest.fixture
def route_to(monkeypatch: pytest.MonkeyPatch):
    """Send the handler's requests to the fake device instead of ip:8080."""

    def _route(server: TestServer) -> None:
        real_post = aiohttp.ClientSession.post

        def post(self: aiohttp.ClientSession, url: Any, **kwargs: Any) -> Any:
            assert url == "http://10.0.0.5:8080/cgi-bin/api/settings"
            return real_post(self, str(server.make_url("/cgi-bin/api/settings")), **kwargs)

        monkeypatch.setattr(aiohttp.ClientSession, "post", post)

    return _route


async def _configure(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    app = web.Application()
    scan.create_scan_routes(app)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/configure-nest", json=body)
        return resp.status, await resp.json()


async def test_mixed_case_key_sent_as_given(route_to) -> None:
    server, received = await _start_device("aBc123XyZ")
    route_to(server)
    try:
        status, data = await _configure({"ip": "10.0.0.5", "api_key": " aBc123XyZ "})
    finally:
        await server.close()
    assert status == 200
    assert data == {"success": True, "device_name": "Hallway"}
    assert [r["api_key"] for r in received] == ["aBc123XyZ"]


async def test_lowercase_serial_retried_upper_cased(route_to) -> None:
    server, received = await _start_device("02AA01AC1234")
    route_to(server)
    try:
        status, data = await _configure({"ip": "10.0.0.5", "api_key": "02aa01ac1234"})
    finally:
        await server.close()
    assert status == 200
    assert data["success"] is True
    assert [r["api_key"] for r in received] == ["02aa01ac1234", "02AA01AC1234"]


async def test_wrong_key_still_401_after_one_retry(route_to) -> None:
    server, received = await _start_device("02AA01AC1234")
    route_to(server)
    try:
        status, data = await _configure({"ip": "10.0.0.5", "api_key": "wrongkey"})
    finally:
        await server.close()
    assert status == 401
    assert data == {"success": False, "auth_required": True}
    assert [r["api_key"] for r in received] == ["wrongkey", "WRONGKEY"]


async def test_upper_case_key_not_retried(route_to) -> None:
    server, received = await _start_device("02AA01AC1234")
    route_to(server)
    try:
        status, _ = await _configure({"ip": "10.0.0.5", "api_key": "02AA01AC9999"})
    finally:
        await server.close()
    assert status == 401
    assert len(received) == 1


async def test_no_key_not_retried(route_to) -> None:
    server, received = await _start_device("02AA01AC1234")
    route_to(server)
    try:
        status, data = await _configure({"ip": "10.0.0.5"})
    finally:
        await server.close()
    assert status == 401
    assert data["auth_required"] is True
    assert len(received) == 1
    assert "api_key" not in received[0]
