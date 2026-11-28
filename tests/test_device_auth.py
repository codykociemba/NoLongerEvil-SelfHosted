"""Tests for device authentication middleware."""

from datetime import datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from nolongerevil.config.environment import settings
from nolongerevil.lib.types import DeviceOwner
from nolongerevil.middleware.device_auth import (
    TIER_PAIRED,
    create_device_auth_middleware,
)


class DummyRequest:
    """Small request stand-in covering the middleware's request interface."""

    def __init__(self, *, serial: str | None = None, storage=None):
        self.headers = {"x-nl-device-serial": serial} if serial else {}
        self.query: dict[str, str] = {}
        self.match_info: dict[str, str] = {}
        self.path = "/nest/transport"
        self.method = "POST"
        self.app = {"storage": storage} if storage else {}
        self.values: dict[str, object] = {}

    def __setitem__(self, key, value):
        self.values[key] = value


@pytest.fixture(autouse=True)
def open_mode(monkeypatch):
    monkeypatch.setattr(settings, "require_device_pairing", False)


@pytest.mark.asyncio
async def test_open_mode_creates_homeassistant_owner():
    storage = SimpleNamespace(
        get_device_owner=AsyncMock(return_value=None),
        set_device_owner=AsyncMock(),
    )
    request = DummyRequest(serial="SERIAL1234", storage=storage)
    handler = AsyncMock(return_value="handled")

    result = await create_device_auth_middleware()(cast(web.Request, request), handler)

    assert result == "handled"
    owner = storage.set_device_owner.await_args.args[0]
    assert owner == DeviceOwner(
        serial="SERIAL1234", user_id="homeassistant", created_at=owner.created_at
    )
    assert isinstance(owner.created_at, datetime)
    assert request.values["device_auth_tier"] == TIER_PAIRED


@pytest.mark.asyncio
async def test_open_mode_preserves_existing_owner():
    existing = DeviceOwner("SERIAL1234", "some-user", datetime.now())
    storage = SimpleNamespace(
        get_device_owner=AsyncMock(return_value=existing),
        set_device_owner=AsyncMock(),
    )
    request = DummyRequest(serial="SERIAL1234", storage=storage)

    await create_device_auth_middleware()(cast(web.Request, request), AsyncMock(return_value="handled"))

    storage.set_device_owner.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_mode_without_serial_passes_without_writing():
    storage = SimpleNamespace(
        get_device_owner=AsyncMock(),
        set_device_owner=AsyncMock(),
    )
    request = DummyRequest(storage=storage)
    handler = AsyncMock(return_value="handled")

    assert await create_device_auth_middleware()(cast(web.Request, request), handler) == "handled"
    storage.get_device_owner.assert_not_awaited()
    storage.set_device_owner.assert_not_awaited()


@pytest.mark.asyncio
async def test_strict_mode_unknown_device_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "require_device_pairing", True)
    storage = SimpleNamespace(
        get_device_owner=AsyncMock(return_value=None),
        get_entry_key_by_serial=AsyncMock(return_value=None),
    )
    request = DummyRequest(serial="SERIAL1234", storage=storage)

    response = await create_device_auth_middleware()(cast(web.Request, request), AsyncMock())

    assert isinstance(response, web.Response)
    assert response.status == 401
