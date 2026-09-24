"""Tests for the Home Assistant Eco switch and Eco preset handling over MQTT."""

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nolongerevil.integrations.mqtt.home_assistant_discovery import (
    get_all_discovery_configs,
    get_discovery_removal_topics,
)
from nolongerevil.integrations.mqtt.mqtt_integration import MqttIntegration
from nolongerevil.lib.types import IntegrationConfig

SERIAL = "02AA01AB471205C0"
ECO_SWITCH_CONFIG = f"homeassistant/switch/nest_{SERIAL}/eco/config"


def make_integration(structure_values: dict[str, Any] | None = None) -> MqttIntegration:
    now = datetime.now()
    config = IntegrationConfig(
        user_id="homeassistant",
        type="mqtt",
        enabled=True,
        config={
            "brokerUrl": "mqtt://localhost:1883",
            "topicPrefix": "nolongerevil",
            "homeAssistantDiscovery": True,
        },
        created_at=now,
        updated_at=now,
    )
    objects = {
        f"device.{SERIAL}": SimpleNamespace(object_key=f"device.{SERIAL}", value={}),
        f"shared.{SERIAL}": SimpleNamespace(
            object_key=f"shared.{SERIAL}", value={"target_temperature_type": "heat"}
        ),
    }
    state_service = MagicMock()
    state_service.get_object.side_effect = lambda _serial, key: objects.get(key)
    state_service.get_objects_by_serial.return_value = (
        [SimpleNamespace(object_key="structure.home", value=structure_values)]
        if structure_values is not None
        else []
    )
    return MqttIntegration(config, state_service, subscription_manager=MagicMock())


class TestEcoSwitchDiscovery:
    def test_eco_switch_is_published(self) -> None:
        configs = dict(get_all_discovery_configs(SERIAL, {}, {}, "nolongerevil"))

        payload = configs[ECO_SWITCH_CONFIG]
        assert payload["state_topic"] == f"nolongerevil/{SERIAL}/ha/eco_switch"
        assert payload["command_topic"] == f"nolongerevil/{SERIAL}/ha/eco_switch/set"

    def test_eco_switch_is_removed_with_device(self) -> None:
        assert ECO_SWITCH_CONFIG in get_discovery_removal_topics(SERIAL)

    def test_leaf_sensor_is_not_named_eco(self) -> None:
        configs = dict(get_all_discovery_configs(SERIAL, {}, {}, "nolongerevil"))

        leaf = configs[f"homeassistant/binary_sensor/nest_{SERIAL}/leaf/config"]
        assert leaf["name"] == "Leaf"


class TestEcoSwitchState:
    async def publish_state(self, structure_values: dict[str, Any] | None) -> str:
        integration = make_integration(structure_values)
        integration._publish_discovery = AsyncMock()  # type: ignore[method-assign]
        client = AsyncMock()

        await integration._publish_ha_state(client, SERIAL)

        published = {c.args[0]: c.args[1] for c in client.publish.await_args_list}
        return published[f"nolongerevil/{SERIAL}/ha/eco_switch"]

    async def test_on_when_manual_eco_active(self) -> None:
        assert await self.publish_state({"manual_eco_all": True}) == "ON"

    async def test_off_when_manual_eco_inactive(self) -> None:
        assert await self.publish_state({"manual_eco_all": False}) == "OFF"

    async def test_off_without_structure(self) -> None:
        assert await self.publish_state(None) == "OFF"


class TestEcoCommands:
    async def run_command(self, command: str, payload: str) -> AsyncMock:
        integration = make_integration()
        with patch(
            "nolongerevil.integrations.mqtt.mqtt_integration.execute_command",
            new_callable=AsyncMock,
        ) as execute:
            await integration._handle_ha_command(f"nolongerevil/{SERIAL}/ha/{command}/set", payload)
        return execute

    @pytest.mark.parametrize(("payload", "away"), [("ON", True), ("OFF", False)])
    async def test_eco_switch_sets_away(self, payload: str, away: bool) -> None:
        execute = await self.run_command("eco_switch", payload)

        execute.assert_awaited_once()
        assert execute.await_args.args[2:] == (SERIAL, "set_away", away)

    async def test_eco_switch_ignores_unknown_payload(self) -> None:
        execute = await self.run_command("eco_switch", "toggle")

        execute.assert_not_awaited()

    async def test_preset_none_leaves_eco(self) -> None:
        execute = await self.run_command("preset", "none")

        execute.assert_awaited_once()
        assert execute.await_args.args[2:] == (SERIAL, "set_away", False)
