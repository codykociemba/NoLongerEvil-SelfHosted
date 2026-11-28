"""Tests for device authentication middleware and open-mode ownership."""

from base64 import b64encode
from datetime import datetime
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from nolongerevil.config.environment import settings
from nolongerevil.lib.types import DeviceObject, DeviceOwner, EntryKey
from nolongerevil.middleware.device_auth import (
    TIER_PAIRED,
    create_device_auth_middleware,
)
from nolongerevil.routes.nest.transport import create_transport_routes
from nolongerevil.services.device_availability import DeviceAvailability
from nolongerevil.services.subscription_manager import SubscriptionManager

SERIAL = "02AA01AB501203EQ"
AUTH = "Basic " + b64encode(f"nest.{SERIAL}:password".encode()).decode()


class DummyRequest:
    """Small request stand-in covering the middleware's request interface."""

    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
        path: str = "/nest/transport",
        method: str = "POST",
        app: dict[str, object] | None = None,
    ):
        self.headers = headers or {}
        self.query = query or {}
        self.match_info: dict[str, str] = {}
        self.path = path
        self.method = method
        self.app = app or {}
        self.values: dict[str, object] = {}

    def __setitem__(self, key, value):
        self.values[key] = value


@pytest.fixture(autouse=True)
def open_mode(monkeypatch):
    monkeypatch.setattr(settings, "require_device_pairing", False)


async def _report_device(state_service, serial: str = SERIAL) -> None:
    await state_service.upsert_object(
        DeviceObject(
            serial=serial,
            object_key=f"device.{serial}",
            object_revision=1,
            object_timestamp=1000,
            value={"away": False},
            updated_at=datetime.now(),
        )
    )


async def _run(request: DummyRequest) -> object:
    handler = AsyncMock(return_value="handled")
    result = await create_device_auth_middleware()(cast(web.Request, request), handler)
    assert result == "handled"
    return result


@pytest.mark.asyncio
async def test_open_mode_assigns_real_thermostat(sqlmodel_service, state_service):
    await _report_device(state_service)
    request = DummyRequest(
        headers={"Authorization": AUTH},
        app={"storage": sqlmodel_service, "state_service": state_service},
    )

    await _run(request)

    owner = await sqlmodel_service.get_device_owner(SERIAL)
    assert owner is not None
    assert owner.user_id == "homeassistant"
    assert owner.open_mode is True
    assert request.values["device_auth_tier"] == TIER_PAIRED
    assert request.values["device_serial"] == SERIAL


@pytest.mark.asyncio
async def test_open_mode_assigns_on_upload_with_client_id(sqlmodel_service, state_service):
    await _report_device(state_service)
    request = DummyRequest(
        headers={"X-nl-client-id": f"d.{SERIAL}.abc"},
        path="/nest/upload",
        app={"storage": sqlmodel_service, "state_service": state_service},
    )

    await _run(request)

    assert (await sqlmodel_service.get_device_owner(SERIAL)) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/nest/ping", "GET"),
        ("/nest/weather/v1", "GET"),
        ("/nest/entry", "GET"),
        ("/nest/passphrase", "GET"),
        ("/nest/transport/device/" + SERIAL, "GET"),
    ],
)
async def test_open_mode_does_not_assign_outside_transport(
    sqlmodel_service, state_service, path, method
):
    await _report_device(state_service)
    request = DummyRequest(
        headers={"Authorization": AUTH},
        path=path,
        method=method,
        app={"storage": sqlmodel_service, "state_service": state_service},
    )

    await _run(request)

    assert (await sqlmodel_service.get_device_owner(SERIAL)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"headers": {"X-NL-Device-Serial": SERIAL}},
        {"query": {"serial": SERIAL}},
    ],
)
async def test_open_mode_ignores_serial_not_from_device_credentials(
    sqlmodel_service, state_service, request_kwargs
):
    await _report_device(state_service)
    request = DummyRequest(
        app={"storage": sqlmodel_service, "state_service": state_service},
        **request_kwargs,
    )

    await _run(request)

    assert (await sqlmodel_service.get_device_owner(SERIAL)) is None


@pytest.mark.asyncio
async def test_open_mode_ignores_serial_never_seen(sqlmodel_service, state_service):
    made_up = "02AA01AB50FFFFFF"
    request = DummyRequest(
        headers={"Authorization": "Basic " + b64encode(f"{made_up}:x".encode()).decode()},
        app={"storage": sqlmodel_service, "state_service": state_service},
    )

    await _run(request)

    assert (await sqlmodel_service.get_device_owner(made_up)) is None
    assert (await sqlmodel_service.get_all_registered_serials()) == []


@pytest.mark.asyncio
async def test_open_mode_preserves_existing_owner(sqlmodel_service, state_service):
    await _report_device(state_service)
    await sqlmodel_service.set_device_owner(DeviceOwner(SERIAL, "some-user", datetime.now()))
    request = DummyRequest(
        headers={"Authorization": AUTH},
        app={"storage": sqlmodel_service, "state_service": state_service},
    )

    await _run(request)

    owner = await sqlmodel_service.get_device_owner(SERIAL)
    assert owner is not None
    assert owner.user_id == "some-user"
    assert owner.open_mode is False


@pytest.mark.asyncio
async def test_open_mode_without_serial_passes_without_writing():
    storage = AsyncMock()
    request = DummyRequest(app={"storage": storage})

    await _run(request)

    storage.get_device_owner.assert_not_awaited()
    storage.set_device_owner.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_mode_moves_default_structure_with_away_state(sqlmodel_service, state_service):
    await _report_device(state_service)
    await state_service.upsert_object(
        DeviceObject(
            serial=SERIAL,
            object_key="structure.default",
            object_revision=4,
            object_timestamp=2000,
            value={"manual_eco_all": True, "manual_eco_timestamp": 1234, "away": True},
            updated_at=datetime.now(),
        )
    )
    request = DummyRequest(
        headers={"Authorization": AUTH},
        app={"storage": sqlmodel_service, "state_service": state_service},
    )

    await _run(request)

    moved = state_service.get_object(SERIAL, "structure.homeassistant")
    assert moved is not None
    assert moved.value["manual_eco_all"] is True
    assert moved.value["manual_eco_timestamp"] == 1234
    assert moved.value["away"] is True
    assert moved.value["devices"] == [SERIAL]
    assert moved.value["name"] == "Home"
    # Persisted, not only cached
    stored = await sqlmodel_service.get_object(SERIAL, "structure.homeassistant")
    assert stored is not None and stored.value["manual_eco_all"] is True


@pytest.mark.asyncio
async def test_open_mode_move_does_not_overwrite_existing_structure(
    sqlmodel_service, state_service
):
    await _report_device(state_service)
    for key, value in (
        ("structure.default", {"manual_eco_all": True}),
        ("structure.homeassistant", {"name": "Mine", "manual_eco_all": False}),
    ):
        await state_service.upsert_object(
            DeviceObject(
                serial=SERIAL,
                object_key=key,
                object_revision=1,
                object_timestamp=1000,
                value=value,
                updated_at=datetime.now(),
            )
        )
    request = DummyRequest(
        headers={"Authorization": AUTH},
        app={"storage": sqlmodel_service, "state_service": state_service},
    )

    await _run(request)

    kept = state_service.get_object(SERIAL, "structure.homeassistant")
    assert kept is not None
    assert kept.value == {"name": "Mine", "manual_eco_all": False}


@pytest.mark.asyncio
async def test_open_mode_owners_removed_when_pairing_required(sqlmodel_service, state_service):
    await _report_device(state_service)
    await _report_device(state_service, "02AA01AB50PAIRED")
    await sqlmodel_service.set_device_owner(
        DeviceOwner("02AA01AB50PAIRED", "homeassistant", datetime.now())
    )
    request = DummyRequest(
        headers={"Authorization": AUTH},
        app={"storage": sqlmodel_service, "state_service": state_service},
    )
    await _run(request)
    assert (await sqlmodel_service.get_device_owner(SERIAL)) is not None

    # What startup does when require_device_pairing is true
    removed = await sqlmodel_service.remove_open_mode_owners()

    assert removed == [SERIAL]
    assert (await sqlmodel_service.get_device_owner(SERIAL)) is None
    # A device that was really paired keeps its owner
    assert (await sqlmodel_service.get_device_owner("02AA01AB50PAIRED")) is not None
    assert (await sqlmodel_service.remove_open_mode_owners()) == []


@pytest.mark.asyncio
async def test_strict_mode_rejects_device_after_open_mode_owner_removed(
    monkeypatch, sqlmodel_service, state_service
):
    await _report_device(state_service)
    await _run(
        DummyRequest(
            headers={"Authorization": AUTH},
            app={"storage": sqlmodel_service, "state_service": state_service},
        )
    )
    await sqlmodel_service.remove_open_mode_owners()

    monkeypatch.setattr(settings, "require_device_pairing", True)
    request = DummyRequest(
        headers={"Authorization": AUTH},
        app={"storage": sqlmodel_service, "state_service": state_service},
    )
    response = await create_device_auth_middleware()(cast(web.Request, request), AsyncMock())

    assert isinstance(response, web.Response)
    assert response.status == 401


@pytest.mark.asyncio
async def test_claim_turns_open_mode_owner_into_paired_owner(sqlmodel_service, state_service):
    """Claiming an entry key replaces the automatic owner with a real one."""
    from nolongerevil.routes.control.registration import handle_register

    await _report_device(state_service)
    await _run(
        DummyRequest(
            headers={"Authorization": AUTH},
            app={"storage": sqlmodel_service, "state_service": state_service},
        )
    )
    now = datetime.now()
    await sqlmodel_service.create_entry_key(
        EntryKey(
            code="ABC1234",
            serial=SERIAL,
            created_at=now,
            expires_at=datetime.fromtimestamp(now.timestamp() + 3600),
        )
    )

    app = web.Application()
    app["storage"] = sqlmodel_service
    app.router.add_post("/register", handle_register)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/register", json={"code": "ABC1234", "userId": "someone"})
        assert resp.status == 200, await resp.text()

    owner = await sqlmodel_service.get_device_owner(SERIAL)
    assert owner is not None
    assert owner.user_id == "someone"
    assert owner.open_mode is False
    assert (await sqlmodel_service.remove_open_mode_owners()) == []


@pytest.mark.asyncio
async def test_subscribe_sends_homeassistant_buckets_after_move(sqlmodel_service, state_service):
    """End to end: an open-mode subscribe gets user + moved structure buckets."""
    await _report_device(state_service)
    await state_service.upsert_object(
        DeviceObject(
            serial=SERIAL,
            object_key="structure.default",
            object_revision=2,
            object_timestamp=2000,
            value={"manual_eco_all": True, "manual_eco_timestamp": 1234},
            updated_at=datetime.now(),
        )
    )
    app = web.Application(middlewares=[create_device_auth_middleware()])
    app["storage"] = sqlmodel_service
    subscription_manager = SubscriptionManager()
    create_transport_routes(
        app, state_service, subscription_manager, DeviceAvailability(subscription_manager)
    )

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/nest/transport",
            headers={"Authorization": AUTH},
            json={"chunked": False, "objects": []},
        )
        assert resp.status == 200
        body = await resp.json()

    by_key = {o["object_key"]: o for o in body["objects"]}
    assert "user.homeassistant" in by_key
    assert "structure.default" not in by_key
    assert by_key["structure.homeassistant"]["value"]["manual_eco_all"] is True
