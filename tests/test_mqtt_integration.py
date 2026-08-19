"""Tests for Home Assistant MQTT state publishing."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nolongerevil.integrations.mqtt.mqtt_integration import MqttIntegration
from nolongerevil.lib.types import DeviceObject, IntegrationConfig


def make_integration(device_values, shared_values, weather):
    """Build an MQTT integration with the minimum state needed by the publisher."""
    serial = "TEST123"
    state_service = SimpleNamespace(
        storage=SimpleNamespace(get_user_weather=AsyncMock(return_value=weather)),
        get_objects_by_serial=lambda requested_serial: [],
        get_object=lambda requested_serial, key: {
            f"device.{serial}": DeviceObject(
                serial, f"device.{serial}", 1, 1, device_values, datetime.now()
            ),
            f"shared.{serial}": DeviceObject(
                serial, f"shared.{serial}", 1, 1, shared_values, datetime.now()
            ),
        }.get(key),
    )
    integration = MqttIntegration(
        IntegrationConfig("user_1", "mqtt", True, {}, datetime.now(), datetime.now()),
        state_service,
    )
    integration._publish_discovery = AsyncMock()
    return integration, serial, state_service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("device", "shared", "weather", "expected"),
    [
        ({}, {}, {"current": {"temp_c": 12}}, "12"),
        ({}, {}, {"current": {"temp_c": 0}}, "0"),
        ({"outdoor_temperature": 0}, {}, {"current": {"temp_c": 12}}, "0"),
        ({"outdoor_temperature": 5}, {}, {"current": {"temp_c": 12}}, "5"),
        ({}, {"outside_temperature": 6}, {"current": {"temp_c": 12}}, "6"),
        ({"outside_temperature": 7}, {}, {"current": {"temp_c": 12}}, "7"),
    ],
)
async def test_outdoor_temperature_uses_weather_fallback_and_preserves_zero(
    device, shared, weather, expected
):
    integration, serial, state_service = make_integration(device, shared, weather)
    client = AsyncMock()

    await integration._publish_ha_state(client, serial)

    published = {
        call.args[0]: call.args[1]
        for call in client.publish.await_args_list
        if call.args[0].endswith("/outdoor_temperature")
    }
    assert published[f"nest/{serial}/ha/outdoor_temperature"] == expected
    if (
        device.get("outdoor_temperature") is None
        and shared.get("outside_temperature") is None
        and device.get("outside_temperature") is None
    ):
        state_service.storage.get_user_weather.assert_awaited_once_with("user_1")
