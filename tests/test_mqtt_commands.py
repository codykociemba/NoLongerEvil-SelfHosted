"""Regression tests for MQTT commands pushed to Nest devices."""

import json
from datetime import datetime

import pytest

from nolongerevil.integrations.mqtt.mqtt_integration import MqttIntegration
from nolongerevil.lib.types import DeviceObject, IntegrationConfig
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.subscription_manager import SubscriptionManager

SERIAL = "TEST12345678"


def mqtt_integration(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> MqttIntegration:
    now = datetime.now()
    config = IntegrationConfig(
        user_id="test",
        type="mqtt",
        enabled=True,
        config={"topicPrefix": "nolongerevil", "publishRaw": True},
        created_at=now,
        updated_at=now,
    )
    return MqttIntegration(config, state_service, subscription_manager)


@pytest.mark.asyncio
async def test_raw_bucket_command_pushes_related_fields_atomically(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> None:
    await state_service.upsert_object(
        DeviceObject(
            serial=SERIAL,
            object_key=f"device.{SERIAL}",
            object_revision=10,
            object_timestamp=1000,
            value={"fan_control_state": False, "fan_timer_timeout": 0},
            updated_at=datetime.now(),
        )
    )
    subscription = await subscription_manager.add_long_poll_subscription(SERIAL, "session")
    assert subscription is not None

    integration = mqtt_integration(state_service, subscription_manager)
    await integration._handle_raw_command(
        f"nolongerevil/{SERIAL}/device/set",
        json.dumps({"fan_control_state": True, "fan_timer_timeout": 2_000_000_000}),
    )

    pushed = subscription.notify_queue.get_nowait()
    assert len(pushed) == 1
    assert pushed[0]["value"]["fan_control_state"] is True
    assert pushed[0]["value"]["fan_timer_timeout"] == 2_000_000_000
    assert subscription.notify_queue.empty()
