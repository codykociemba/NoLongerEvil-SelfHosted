from datetime import datetime
from unittest.mock import AsyncMock

from nolongerevil.integrations.mqtt.mqtt_integration import MqttIntegration
from nolongerevil.lib.types import DeviceObject, IntegrationConfig

SERIAL = "TEST12345678"


def integration(state_service) -> MqttIntegration:
    now = datetime.now()
    config = IntegrationConfig(
        user_id="test",
        type="mqtt",
        enabled=True,
        config={"topicPrefix": "nolongerevil", "homeAssistantDiscovery": True},
        created_at=now,
        updated_at=now,
    )
    return MqttIntegration(config, state_service, None)


async def test_unchanged_discovery_is_not_republished(state_service):
    await state_service.upsert_object(
        DeviceObject(
            serial=SERIAL,
            object_key=f"shared.{SERIAL}",
            object_revision=1,
            object_timestamp=1000,
            value={"target_temperature_type": "heat"},
            updated_at=datetime.now(),
        )
    )
    mqtt = integration(state_service)
    client = AsyncMock()

    await mqtt._publish_discovery(client, SERIAL)
    count = client.publish.await_count
    await mqtt._publish_discovery(client, SERIAL)
    assert client.publish.await_count == count

    state_service.get_object(SERIAL, f"shared.{SERIAL}").value["target_temperature_type"] = "range"
    await mqtt._publish_discovery(client, SERIAL)
    assert client.publish.await_count > count
