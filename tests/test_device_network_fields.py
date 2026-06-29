"""Tests for local_ip and mac_address exposure in /api/devices and /status responses.

These fields live in the device.{serial} bucket (PUT by firmware on every state change)
but were not surfaced in the Control API responses. format_device_status() now extracts
them; both routes go through it, so both endpoints are asserted here to prevent a
future refactor from dropping one.
"""

import json
import time
from datetime import datetime
from unittest.mock import Mock

import pytest
from aiohttp import web

from nolongerevil.lib.types import DeviceObject
from nolongerevil.routes.control.status import handle_devices, handle_status
from nolongerevil.services.device_availability import DeviceAvailability
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.subscription_manager import SubscriptionManager

SERIAL = "02AA01AB501203EQ"
LOCAL_IP = "192.168.1.42"
# Bare-hex (no separators), matching the firmware's wire format —
# verified against a live Gen 1 device on 2026-05-20.
MAC_ADDRESS = "18b430000000"


def _make_status_request(
    state_service: DeviceStateService,
    device_availability: DeviceAvailability,
    serial: str = SERIAL,
) -> Mock:
    req = Mock(spec=web.Request)
    req.query = {"serial": serial}
    req.app = {
        "state_service": state_service,
        "device_availability": device_availability,
    }
    return req


def _make_devices_request(
    state_service: DeviceStateService,
    device_availability: DeviceAvailability,
    subscription_manager: SubscriptionManager,
) -> Mock:
    req = Mock(spec=web.Request)
    # 'storage' key omitted so handle_devices falls back to
    # state_service.get_all_serials() instead of requiring a pairing storage layer.
    req.app = {
        "state_service": state_service,
        "device_availability": device_availability,
        "subscription_manager": subscription_manager,
    }
    return req


async def _seed_device(
    state_service: DeviceStateService,
    *,
    serial: str = SERIAL,
    extra_values: dict | None = None,
) -> None:
    values: dict = {"current_temperature": 21.0, "target_temperature": 22.0}
    if extra_values:
        values.update(extra_values)
    await state_service.upsert_object(
        DeviceObject(
            serial=serial,
            object_key=f"device.{serial}",
            object_revision=1,
            object_timestamp=int(time.time() * 1000),
            value=values,
            updated_at=datetime.now(),
        )
    )


@pytest.mark.asyncio
async def test_status_surfaces_local_ip_and_mac_when_present(
    state_service: DeviceStateService,
    device_availability: DeviceAvailability,
) -> None:
    await _seed_device(
        state_service,
        extra_values={"local_ip": LOCAL_IP, "mac_address": MAC_ADDRESS},
    )

    resp = await handle_status(_make_status_request(state_service, device_availability))
    body = json.loads(resp.body)

    assert body["local_ip"] == LOCAL_IP
    assert body["mac_address"] == MAC_ADDRESS


@pytest.mark.asyncio
async def test_status_local_ip_and_mac_null_when_absent(
    state_service: DeviceStateService,
    device_availability: DeviceAvailability,
) -> None:
    await _seed_device(state_service)

    resp = await handle_status(_make_status_request(state_service, device_availability))
    body = json.loads(resp.body)

    assert "local_ip" in body
    assert body["local_ip"] is None
    assert "mac_address" in body
    assert body["mac_address"] is None


@pytest.mark.asyncio
async def test_devices_endpoint_surfaces_local_ip_and_mac(
    state_service: DeviceStateService,
    device_availability: DeviceAvailability,
    subscription_manager: SubscriptionManager,
) -> None:
    await _seed_device(
        state_service,
        extra_values={"local_ip": LOCAL_IP, "mac_address": MAC_ADDRESS},
    )

    resp = await handle_devices(
        _make_devices_request(state_service, device_availability, subscription_manager)
    )
    body = json.loads(resp.body)

    assert body["total"] == 1
    device = body["devices"][0]
    assert device["serial"] == SERIAL
    assert device["local_ip"] == LOCAL_IP
    assert device["mac_address"] == MAC_ADDRESS
