"""Tests for MQTT integration availability and command subscription handling."""

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nolongerevil.integrations.integration_manager import IntegrationManager
from nolongerevil.integrations.mqtt.mqtt_integration import MqttIntegration
from nolongerevil.lib.types import IntegrationConfig

SERIAL = "02AA01AB471205C0"


def make_config(**overrides: Any) -> IntegrationConfig:
    config: dict[str, Any] = {
        "brokerUrl": "mqtt://localhost:1883",
        "topicPrefix": "nolongerevil",
        "homeAssistantDiscovery": True,
        "publishRaw": True,
    }
    config.update(overrides)
    now = datetime.now()
    return IntegrationConfig(
        user_id="homeassistant",
        type="mqtt",
        enabled=True,
        config=config,
        created_at=now,
        updated_at=now,
    )


def make_state_service() -> MagicMock:
    """State service with one known device that has no objects yet."""
    state_service = MagicMock()
    state_service.get_all_serials.return_value = [SERIAL]
    state_service.get_object.return_value = None
    return state_service


def published(client: AsyncMock) -> dict[str, Any]:
    return {c.args[0]: c.args[1] for c in client.publish.await_args_list}


class TestInitialAvailability:
    """Availability published on MQTT (re)connect must reflect the device."""

    async def test_publishes_offline_when_device_not_connected(self) -> None:
        integration = MqttIntegration(
            make_config(),
            make_state_service(),
            availability_checker=lambda _serial: False,
        )
        client = AsyncMock()

        await integration._publish_initial_state(client)

        assert published(client)[f"nolongerevil/{SERIAL}/availability"] == "offline"

    async def test_publishes_online_when_device_connected(self) -> None:
        integration = MqttIntegration(
            make_config(),
            make_state_service(),
            availability_checker=lambda _serial: True,
        )
        client = AsyncMock()

        await integration._publish_initial_state(client)

        assert published(client)[f"nolongerevil/{SERIAL}/availability"] == "online"

    async def test_defaults_to_online_without_checker(self) -> None:
        integration = MqttIntegration(make_config(), make_state_service())
        client = AsyncMock()

        await integration._publish_initial_state(client)

        assert published(client)[f"nolongerevil/{SERIAL}/availability"] == "online"

    async def test_checker_error_falls_back_to_online(self) -> None:
        def broken(_serial: str) -> bool:
            raise RuntimeError("boom")

        integration = MqttIntegration(
            make_config(), make_state_service(), availability_checker=broken
        )
        client = AsyncMock()

        await integration._publish_initial_state(client)

        assert published(client)[f"nolongerevil/{SERIAL}/availability"] == "online"


class TestIntegrationManagerWiring:
    def test_passes_device_availability_to_mqtt(self) -> None:
        availability = MagicMock()
        availability.is_available.return_value = False
        manager = IntegrationManager(
            MagicMock(), make_state_service(), MagicMock(), device_availability=availability
        )

        integration = manager._instantiate_integration(make_config())

        assert isinstance(integration, MqttIntegration)
        assert integration._is_device_available(SERIAL) is False
        availability.is_available.assert_called_once_with(SERIAL)


class TestCommandSubscriptions:
    """Each command topic must be matched by exactly one subscription."""

    @pytest.mark.parametrize(
        ("publish_raw", "ha_discovery", "expected"),
        [
            (True, True, ["nolongerevil/+/+/+/set"]),
            (True, False, ["nolongerevil/+/+/+/set"]),
            (False, True, ["nolongerevil/+/ha/+/set"]),
            (False, False, []),
        ],
    )
    def test_subscriptions(
        self, publish_raw: bool, ha_discovery: bool, expected: list[str]
    ) -> None:
        integration = MqttIntegration(
            make_config(publishRaw=publish_raw, homeAssistantDiscovery=ha_discovery),
            make_state_service(),
        )

        assert integration._command_subscriptions() == expected

    async def test_subscribes_once_when_raw_and_ha_enabled(self) -> None:
        integration = MqttIntegration(make_config(), make_state_service())
        client = AsyncMock()

        await integration._subscribe_to_commands(client)

        client.subscribe.assert_awaited_once_with("nolongerevil/+/+/+/set")


class TestMessageRouting:
    @staticmethod
    def message(topic: str, payload: bytes = b"20") -> MagicMock:
        msg = MagicMock()
        msg.topic = topic
        msg.payload = payload
        return msg

    @staticmethod
    def integration(**overrides: Any) -> MqttIntegration:
        integration = MqttIntegration(make_config(**overrides), make_state_service())
        integration._handle_ha_command = AsyncMock()  # type: ignore[method-assign]
        integration._handle_raw_command = AsyncMock()  # type: ignore[method-assign]
        return integration

    async def test_ha_topic_routes_to_ha_handler_only(self) -> None:
        integration = self.integration()

        await integration._handle_message(
            AsyncMock(), self.message(f"nolongerevil/{SERIAL}/ha/target_temperature/set")
        )

        integration._handle_ha_command.assert_awaited_once()
        integration._handle_raw_command.assert_not_awaited()

    async def test_raw_topic_routes_to_raw_handler(self) -> None:
        integration = self.integration()

        await integration._handle_message(
            AsyncMock(), self.message(f"nolongerevil/{SERIAL}/shared/target_temperature/set")
        )

        integration._handle_raw_command.assert_awaited_once()
        integration._handle_ha_command.assert_not_awaited()

    async def test_ha_topic_ignored_when_ha_discovery_disabled(self) -> None:
        integration = self.integration(homeAssistantDiscovery=False)

        await integration._handle_message(
            AsyncMock(), self.message(f"nolongerevil/{SERIAL}/ha/target_temperature/set")
        )

        integration._handle_ha_command.assert_not_awaited()
        integration._handle_raw_command.assert_not_awaited()

    async def test_raw_topic_ignored_when_raw_disabled(self) -> None:
        integration = self.integration(publishRaw=False)

        await integration._handle_message(
            AsyncMock(), self.message(f"nolongerevil/{SERIAL}/shared/target_temperature/set")
        )

        integration._handle_raw_command.assert_not_awaited()
