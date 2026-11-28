"""Tests for mqtt_integration.py's tolerance of MAC-alias-migrated object_key casing.

Mirrors the rationale in test_schedule_lookup.py: device-originated buckets
(device.*, shared.*) may be stored as "<bucket>.<serial-lower>" after MAC-alias
migration, while MQTT topics/commands address devices by their uppercase serial.
get_object_by_prefix bridges that gap for MQTT discovery, state publishing, and
command handling.
"""

from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from nolongerevil.integrations.mqtt.mqtt_integration import MqttIntegration
from nolongerevil.lib.types import DeviceObject, IntegrationConfig
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.subscription_manager import SubscriptionManager

SERIAL = "02AA01AC43140GH2"
SERIAL_LOWER = SERIAL.lower()


def _make_integration(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> MqttIntegration:
    config = IntegrationConfig(
        user_id="user1",
        type="mqtt",
        enabled=True,
        config={
            "topicPrefix": "nest",
            "homeAssistantDiscovery": True,
            "publishRaw": True,
        },
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    return MqttIntegration(config, state_service, subscription_manager)


async def _seed_device_and_shared(
    state_service: DeviceStateService,
    device_value: dict | None = None,
    shared_value: dict | None = None,
    revision: int = 1,
) -> None:
    await state_service.upsert_object(
        DeviceObject(
            serial=SERIAL,
            object_key=f"device.{SERIAL_LOWER}",
            object_revision=revision,
            object_timestamp=1234567890,
            value=device_value or {},
            updated_at=datetime.now(),
        )
    )
    await state_service.upsert_object(
        DeviceObject(
            serial=SERIAL,
            object_key=f"shared.{SERIAL_LOWER}",
            object_revision=revision,
            object_timestamp=1234567890,
            value=shared_value or {},
            updated_at=datetime.now(),
        )
    )


@pytest.mark.asyncio
async def test_publish_discovery_uses_lowercase_keyed_objects(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> None:
    """_publish_discovery finds device.<serial-lower>/shared.<serial-lower>
    and passes their values to get_all_discovery_configs."""
    await _seed_device_and_shared(
        state_service,
        device_value={"has_fan": True},
        shared_value={"target_temperature_type": "heat"},
    )
    integration = _make_integration(state_service, subscription_manager)
    client = Mock()
    client.publish = AsyncMock()

    with patch(
        "nolongerevil.integrations.mqtt.mqtt_integration.get_all_discovery_configs",
        return_value=[],
    ) as mock_get_configs:
        await integration._publish_discovery(client, SERIAL)

    args, _ = mock_get_configs.call_args
    assert args[1] == {"has_fan": True}
    assert args[2] == {"target_temperature_type": "heat"}


@pytest.mark.asyncio
async def test_publish_ha_state_finds_lowercase_keyed_objects(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_publish_ha_state doesn't bail out with "missing objects" when the
    device's buckets are stored under "<bucket>.<serial-lower>"."""
    await _seed_device_and_shared(
        state_service,
        device_value={},
        shared_value={"target_temperature_type": "heat"},
    )
    integration = _make_integration(state_service, subscription_manager)
    client = Mock()
    client.publish = AsyncMock()

    with patch.object(integration, "_publish_discovery", new=AsyncMock()):
        await integration._publish_ha_state(client, SERIAL)

    assert "missing objects" not in caplog.text
    published_topics = [call.args[0] for call in client.publish.call_args_list]
    assert f"nest/{SERIAL}/ha/mode" in published_topics


@pytest.mark.asyncio
async def test_publish_initial_state_finds_lowercase_keyed_objects(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> None:
    """_publish_initial_state finds device.<serial-lower>/shared.<serial-lower>
    and publishes raw state for both buckets."""
    await _seed_device_and_shared(
        state_service,
        device_value={"foo": "bar"},
        shared_value={"baz": "qux"},
    )
    integration = _make_integration(state_service, subscription_manager)
    client = Mock()
    client.publish = AsyncMock()

    with (
        patch.object(integration, "_publish_raw_state", new=AsyncMock()) as mock_raw,
        patch.object(integration, "_publish_ha_state", new=AsyncMock()),
    ):
        await integration._publish_initial_state(client)

    object_types_published = [call.args[2] for call in mock_raw.call_args_list]
    assert "device" in object_types_published
    assert "shared" in object_types_published


@pytest.mark.asyncio
async def test_handle_ha_command_set_mode_updates_lowercase_shared_key(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> None:
    """An HA "mode" command updates the existing "shared.<serial-lower>"
    object in place rather than creating a "shared.<SERIAL>" duplicate."""
    await _seed_device_and_shared(
        state_service,
        device_value={},
        shared_value={"target_temperature_type": "off"},
        revision=5,
    )
    integration = _make_integration(state_service, subscription_manager)
    subscription_manager.notify_all_subscribers = AsyncMock(return_value=0)

    with patch.object(integration, "_publish_ha_state", new=AsyncMock()):
        await integration._handle_ha_command(f"nest/{SERIAL}/ha/mode/set", "heat")

    updated = state_service.get_object(SERIAL, f"shared.{SERIAL_LOWER}")
    assert updated is not None
    assert updated.value["target_temperature_type"] == "heat"
    assert state_service.get_object(SERIAL, f"shared.{SERIAL}") is None


@pytest.mark.asyncio
async def test_handle_ha_command_fan_duration_updates_lowercase_device_key(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> None:
    """An HA "fan_duration" command (handled via _update_device_value) updates
    the existing "device.<serial-lower>" object in place."""
    await _seed_device_and_shared(
        state_service,
        device_value={"fan_timer_duration_minutes": 15, "fan_timer_timeout": 0},
        shared_value={"target_temperature_type": "off"},
        revision=2,
    )
    integration = _make_integration(state_service, subscription_manager)
    subscription_manager.notify_all_subscribers = AsyncMock(return_value=0)

    with patch.object(integration, "_publish_ha_state", new=AsyncMock()):
        await integration._handle_ha_command(f"nest/{SERIAL}/ha/fan_duration/set", "30")

    updated = state_service.get_object(SERIAL, f"device.{SERIAL_LOWER}")
    assert updated is not None
    assert updated.value["fan_timer_duration_minutes"] == 30
    assert state_service.get_object(SERIAL, f"device.{SERIAL}") is None


@pytest.mark.asyncio
async def test_handle_raw_command_updates_lowercase_keyed_object_in_place(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> None:
    """A raw MQTT command (.../device/<field>/set) updates the existing
    "device.<serial-lower>" object in place rather than creating a
    "device.<SERIAL>" duplicate."""
    await _seed_device_and_shared(
        state_service,
        device_value={"some_field": "old"},
        shared_value={},
        revision=7,
    )
    integration = _make_integration(state_service, subscription_manager)
    subscription_manager.notify_all_subscribers = AsyncMock(return_value=0)

    await integration._handle_raw_command(f"nest/{SERIAL}/device/some_field/set", "new")

    updated = state_service.get_object(SERIAL, f"device.{SERIAL_LOWER}")
    assert updated is not None
    assert updated.value["some_field"] == "new"
    assert updated.object_revision == 8
    assert state_service.get_object(SERIAL, f"device.{SERIAL}") is None
