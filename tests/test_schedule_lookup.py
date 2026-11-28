"""Tests for schedule lookup, tolerant of MAC-alias-migrated object_key casing.

MAC-alias migration rewrites a device's own buckets (device.*, shared.*,
schedule.*) using the device's lowercase serial, e.g. "schedule.<serial-lower>".
But set_schedule/handle_schedule historically addressed the schedule bucket as
"schedule.<SERIAL>" (uppercase, as passed by the UI). get_object_by_prefix
bridges that gap so the schedule is found and updated regardless of casing.
"""

from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web

from nolongerevil.lib.types import DeviceObject
from nolongerevil.routes.control.command import execute_command
from nolongerevil.routes.control.status import handle_schedule
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.subscription_manager import SubscriptionManager

SERIAL = "02AA01AC43140GH2"
SERIAL_LOWER = SERIAL.lower()

SCHEDULE_VALUE = {
    "ver": 2,
    "name": "Current Schedule",
    "schedule_mode": "HEAT",
    "days": {"0": [{"time": 0, "type": "HEAT", "temp": 20.0}]},
}


def _make_schedule_request(state_service: DeviceStateService, serial: str) -> Mock:
    req = Mock(spec=web.Request)
    req.query = {"serial": serial}
    req.app = {"state_service": state_service}
    return req


@pytest.mark.asyncio
async def test_handle_schedule_finds_lowercase_migrated_key(
    state_service: DeviceStateService,
) -> None:
    """A schedule stored under "schedule.<serial-lower>" (as produced by
    MAC-alias migration) is returned for a GET with the uppercase serial."""
    await state_service.upsert_object(
        DeviceObject(
            serial=SERIAL,
            object_key=f"schedule.{SERIAL_LOWER}",
            object_revision=1,
            object_timestamp=1234567890,
            value=SCHEDULE_VALUE,
            updated_at=datetime.now(),
        )
    )

    resp = await handle_schedule(_make_schedule_request(state_service, SERIAL))

    assert resp.status == 200
    import json

    body = json.loads(resp.body)
    assert body["schedule"] == SCHEDULE_VALUE


@pytest.mark.asyncio
async def test_handle_schedule_no_schedule_returns_none(
    state_service: DeviceStateService,
) -> None:
    """With no schedule object stored, the endpoint reports schedule: None."""
    resp = await handle_schedule(_make_schedule_request(state_service, SERIAL))

    assert resp.status == 200
    import json

    body = json.loads(resp.body)
    assert body["schedule"] is None


@pytest.mark.asyncio
async def test_set_schedule_updates_existing_lowercase_key(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> None:
    """set_schedule updates the existing "schedule.<serial-lower>" object
    in place rather than creating a separate "schedule.<SERIAL>" object."""
    await state_service.upsert_object(
        DeviceObject(
            serial=SERIAL,
            object_key=f"schedule.{SERIAL_LOWER}",
            object_revision=3,
            object_timestamp=1234567890,
            value=SCHEDULE_VALUE,
            updated_at=datetime.now(),
        )
    )

    new_value = {**SCHEDULE_VALUE, "name": "Updated Schedule"}
    subscription_manager.notify_all_subscribers = AsyncMock(return_value=0)

    result = await execute_command(state_service, subscription_manager, SERIAL, "set_schedule", new_value)

    assert result["object_key"] == f"schedule.{SERIAL_LOWER}"

    updated = state_service.get_object(SERIAL, f"schedule.{SERIAL_LOWER}")
    assert updated is not None
    assert updated.value["name"] == "Updated Schedule"
    assert updated.object_revision == 4

    # No separate uppercase-keyed object should have been created
    assert state_service.get_object(SERIAL, f"schedule.{SERIAL}") is None


@pytest.mark.asyncio
async def test_set_schedule_creates_uppercase_key_when_none_exists(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> None:
    """With no existing schedule object, set_schedule falls back to the
    historical "schedule.<SERIAL>" naming."""
    subscription_manager.notify_all_subscribers = AsyncMock(return_value=0)

    result = await execute_command(state_service, subscription_manager, SERIAL, "set_schedule", SCHEDULE_VALUE)

    assert result["object_key"] == f"schedule.{SERIAL}"
    assert state_service.get_object(SERIAL, f"schedule.{SERIAL}") is not None


@pytest.mark.asyncio
async def test_set_fan_finds_capability_and_updates_lowercase_device_key(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> None:
    """set_fan (a "device" bucket command) must find has_fan from the
    MAC-alias-migrated "device.<serial-lower>" object, and update it in
    place rather than creating a "device.<SERIAL>" duplicate."""
    await state_service.upsert_object(
        DeviceObject(
            serial=SERIAL,
            object_key=f"device.{SERIAL_LOWER}",
            object_revision=5,
            object_timestamp=1234567890,
            value={"has_fan": True, "fan_timer_duration_minutes": 30},
            updated_at=datetime.now(),
        )
    )
    subscription_manager.notify_all_subscribers = AsyncMock(return_value=0)

    result = await execute_command(state_service, subscription_manager, SERIAL, "set_fan", "on")

    assert result["object_key"] == f"device.{SERIAL_LOWER}"
    updated = state_service.get_object(SERIAL, f"device.{SERIAL_LOWER}")
    assert updated is not None
    assert updated.value["fan_timer_timeout"] > 0
    assert updated.object_revision == 6
    assert state_service.get_object(SERIAL, f"device.{SERIAL}") is None


@pytest.mark.asyncio
async def test_set_temperature_updates_lowercase_shared_key(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> None:
    """set_temperature (a "shared" bucket command) updates the existing
    "shared.<serial-lower>" object in place rather than creating a
    "shared.<SERIAL>" duplicate."""
    await state_service.upsert_object(
        DeviceObject(
            serial=SERIAL,
            object_key=f"shared.{SERIAL_LOWER}",
            object_revision=10,
            object_timestamp=1234567890,
            value={"current_temperature": 20.0, "target_temperature": 19.0},
            updated_at=datetime.now(),
        )
    )
    subscription_manager.notify_all_subscribers = AsyncMock(return_value=0)

    result = await execute_command(state_service, subscription_manager, SERIAL, "set_temperature", 21.0)

    assert result["object_key"] == f"shared.{SERIAL_LOWER}"
    updated = state_service.get_object(SERIAL, f"shared.{SERIAL_LOWER}")
    assert updated is not None
    assert updated.value["target_temperature"] == 21.0
    assert updated.object_revision == 11
    assert state_service.get_object(SERIAL, f"shared.{SERIAL}") is None
