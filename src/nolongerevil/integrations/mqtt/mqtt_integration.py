"""MQTT integration for Home Assistant and other MQTT consumers."""

import asyncio
import contextlib
import json
import re
import ssl
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import aiomqtt

from nolongerevil.integrations.base_integration import BaseIntegration
from nolongerevil.integrations.mqtt.helpers import (
    battery_voltage_to_percent,
    derive_hvac_action,
    get_fan_mode,
    get_preset_mode,
    ha_mode_to_nest,
    is_device_away,
    is_eco_active,
    is_fan_running,
    nest_mode_to_ha,
)
from nolongerevil.integrations.mqtt.home_assistant_discovery import (
    get_all_discovery_configs,
    get_discovery_removal_topics,
)
from nolongerevil.integrations.mqtt.topic_builder import (
    build_availability_topic,
    build_state_topic,
    parse_object_key,
)
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import DeviceStateChange, IntegrationConfig

if TYPE_CHECKING:
    from nolongerevil.services.device_state_service import DeviceStateService

logger = get_logger(__name__)


class MqttIntegration(BaseIntegration):
    """MQTT integration for publishing device state and receiving commands."""

    def __init__(
        self,
        config: IntegrationConfig,
        state_service: "DeviceStateService",
    ) -> None:
        """Initialize the MQTT integration.

        Args:
            config: Integration configuration
            state_service: Device state service
        """
        super().__init__(config)
        self._state_service = state_service
        self._client: aiomqtt.Client | None = None
        self._active_client: aiomqtt.Client | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._connected = False

        # Parse configuration with TypeScript-matching defaults
        self._broker_url = self.get_config_value("brokerUrl", "mqtt://localhost:1883")
        self._topic_prefix = self.get_config_value("topicPrefix", "nest")
        self._discovery_prefix = self.get_config_value("discoveryPrefix", "homeassistant")
        self._username = self.get_config_value("username")
        self._password = self.get_config_value("password")
        self._ha_discovery = self.get_config_value("homeAssistantDiscovery", False)
        self._publish_raw = self.get_config_value("publishRaw", True)

    async def initialize(self) -> None:
        """Initialize the MQTT connection."""
        try:
            await self._connect()
            logger.info(f"MQTT integration initialized for {self._broker_url}")
        except Exception as e:
            logger.error(f"Failed to initialize MQTT integration: {e}")
            raise

    async def shutdown(self) -> None:
        """Shutdown the MQTT connection."""
        self._connected = False

        if self._listener_task:
            self._listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener_task
            self._listener_task = None

        self._client = None
        logger.info("MQTT integration shut down")

    async def _connect(self) -> None:
        """Establish MQTT connection."""
        parsed = urlparse(self._broker_url)

        hostname = parsed.hostname or "localhost"
        port = parsed.port or (8883 if parsed.scheme == "mqtts" else 1883)

        tls_context = None
        if parsed.scheme == "mqtts":
            tls_context = ssl.create_default_context()

        self._client = aiomqtt.Client(
            hostname=hostname,
            port=port,
            username=self._username,
            password=self._password,
            tls_context=tls_context,
        )

        self._listener_task = asyncio.create_task(self._run_client())

    async def _run_client(self) -> None:
        """Run the MQTT client and message listener."""
        while self.enabled:
            try:
                if self._client is None:
                    logger.error("MQTT client not initialized")
                    return
                async with self._client as client:
                    self._active_client = client
                    self._connected = True
                    logger.info("MQTT connected")

                    # Subscribe to command topics
                    await self._subscribe_to_commands(client)

                    # Publish discovery and initial state for all known devices
                    if self._ha_discovery:
                        await self._publish_all_discoveries(client)

                    await self._publish_initial_state(client)

                    # Listen for messages
                    async for message in client.messages:
                        await self._handle_message(client, message)

            except aiomqtt.MqttError as e:
                logger.error(f"MQTT connection error: {e}")
                self._connected = False
                self._active_client = None
                if self.enabled:
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                break

    async def _subscribe_to_commands(self, client: aiomqtt.Client) -> None:
        """Subscribe to command topics."""
        prefix = self._topic_prefix

        # Raw command topics
        if self._publish_raw:
            await client.subscribe(f"{prefix}/+/+/+/set")
            logger.debug(f"Subscribed to {prefix}/+/+/+/set")

        # HA command topics
        if self._ha_discovery:
            await client.subscribe(f"{prefix}/+/ha/+/set")
            logger.debug(f"Subscribed to {prefix}/+/ha/+/set")

    async def _handle_message(
        self,
        client: aiomqtt.Client,
        message: aiomqtt.Message,
    ) -> None:
        """Handle incoming MQTT message."""
        topic = str(message.topic)
        raw_payload = message.payload
        if isinstance(raw_payload, (bytes, bytearray)):
            payload = raw_payload.decode()
        elif raw_payload:
            payload = str(raw_payload)
        else:
            payload = ""

        # Handle HA command topics
        if "/ha/" in topic and topic.endswith("/set"):
            await self._handle_ha_command(topic, payload)
            return

        # Handle raw command topics
        if topic.endswith("/set"):
            await self._handle_raw_command(topic, payload)

    async def _handle_ha_command(self, topic: str, payload: str) -> None:
        """Handle Home Assistant formatted command."""
        prefix = self._topic_prefix
        escaped_prefix = re.escape(prefix)
        match = re.match(rf"^{escaped_prefix}/([^/]+)/ha/(.+)/set$", topic)
        if not match:
            logger.warning(f"Invalid HA command topic: {topic}")
            return

        serial, command = match.groups()
        logger.info(f"HA Command: {serial}/{command} = {payload}")

        device_obj = self._state_service.get_object(serial, f"device.{serial}")
        shared_obj = self._state_service.get_object(serial, f"shared.{serial}")

        if not device_obj or not shared_obj:
            logger.warning(f"Device {serial} not fully initialized")
            return

        if command == "mode":
            nest_mode = ha_mode_to_nest(payload)
            await self._update_shared_value(
                serial, shared_obj, "target_temperature_type", nest_mode
            )

        elif command == "target_temperature":
            temp = float(payload)
            await self._update_shared_value(serial, shared_obj, "target_temperature", temp)

        elif command == "target_temperature_low":
            temp = float(payload)
            await self._update_shared_value(serial, shared_obj, "target_temperature_low", temp)

        elif command == "target_temperature_high":
            temp = float(payload)
            await self._update_shared_value(serial, shared_obj, "target_temperature_high", temp)

        elif command == "fan_mode":
            if payload.lower() == "on":
                timeout_timestamp = int(time.time()) + 3600
                await self._update_device_fields(
                    serial,
                    device_obj,
                    {
                        "fan_control_state": True,
                        "fan_timer_active": True,
                        "fan_timer_timeout": timeout_timestamp,
                    },
                )
            else:
                await self._update_device_fields(
                    serial,
                    device_obj,
                    {
                        "fan_control_state": False,
                        "fan_timer_active": False,
                        "fan_timer_timeout": 0,
                    },
                )

        elif command == "preset":
            if payload.lower() == "away":
                await self._update_device_fields(
                    serial,
                    device_obj,
                    {
                        "auto_away": 2,
                        "away": True,
                    },
                )
            elif payload.lower() == "home":
                await self._update_device_fields(
                    serial,
                    device_obj,
                    {
                        "auto_away": 0,
                        "away": False,
                    },
                )
            elif payload.lower() == "eco":
                await self._update_device_value(
                    serial, device_obj, "eco", {"mode": "manual-eco", "leaf": True}
                )

        else:
            logger.warning(f"Unknown HA command: {command}")

        # Republish state to reflect changes
        if self._ha_discovery and self._active_client:
            await self._publish_ha_state(self._active_client, serial)

    async def _handle_raw_command(self, topic: str, payload: str) -> None:
        """Handle raw MQTT command."""
        prefix = self._topic_prefix
        escaped_prefix = re.escape(prefix)
        match = re.match(rf"^{escaped_prefix}/([^/]+)/([^/]+)/([^/]+)/set$", topic)
        if not match:
            return

        serial, object_type, field = match.groups()

        # Parse value
        value: Any = payload
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            import contextlib

            with contextlib.suppress(ValueError):
                value = float(payload)

        logger.info(f"Raw Command: {serial}/{object_type}.{field} = {value}")

        from datetime import datetime

        from nolongerevil.lib.types import DeviceObject

        object_key = f"{object_type}.{serial}"
        current_obj = self._state_service.get_object(serial, object_key)

        if not current_obj:
            logger.warning(f"Object not found: {object_key}")
            return

        new_value = {**current_obj.value, field: value}
        new_revision = current_obj.object_revision + 1
        new_timestamp = int(time.time() * 1000)

        obj = DeviceObject(
            serial=serial,
            object_key=object_key,
            object_revision=new_revision,
            object_timestamp=new_timestamp,
            value=new_value,
            updated_at=datetime.utcnow(),
        )
        await self._state_service.upsert_object(obj)
        logger.info(f"Applied raw command to {serial}: {{{field}: {value}}}")

    async def _update_shared_value(
        self, serial: str, current_obj: Any, field: str, value: Any
    ) -> None:
        """Update a field in the shared object."""
        from datetime import datetime

        from nolongerevil.lib.types import DeviceObject

        object_key = f"shared.{serial}"
        new_value = {**current_obj.value, field: value}
        new_revision = current_obj.object_revision + 1
        new_timestamp = int(time.time() * 1000)

        obj = DeviceObject(
            serial=serial,
            object_key=object_key,
            object_revision=new_revision,
            object_timestamp=new_timestamp,
            value=new_value,
            updated_at=datetime.utcnow(),
        )
        await self._state_service.upsert_object(obj)
        logger.info(f"Applied MQTT command to {serial}: {{{field}: {value}}}")

    async def _update_device_value(
        self, serial: str, current_obj: Any, field: str, value: Any
    ) -> None:
        """Update a field in the device object."""
        from datetime import datetime

        from nolongerevil.lib.types import DeviceObject

        object_key = f"device.{serial}"
        new_value = {**current_obj.value, field: value}
        new_revision = current_obj.object_revision + 1
        new_timestamp = int(time.time() * 1000)

        obj = DeviceObject(
            serial=serial,
            object_key=object_key,
            object_revision=new_revision,
            object_timestamp=new_timestamp,
            value=new_value,
            updated_at=datetime.utcnow(),
        )
        await self._state_service.upsert_object(obj)
        logger.info(f"Applied MQTT command to {serial}: {{{field}: {value}}}")

    async def _update_device_fields(
        self, serial: str, current_obj: Any, fields: dict[str, Any]
    ) -> None:
        """Update multiple fields in the device object atomically."""
        from datetime import datetime

        from nolongerevil.lib.types import DeviceObject

        object_key = f"device.{serial}"
        new_value = {**current_obj.value, **fields}
        new_revision = current_obj.object_revision + 1
        new_timestamp = int(time.time() * 1000)

        obj = DeviceObject(
            serial=serial,
            object_key=object_key,
            object_revision=new_revision,
            object_timestamp=new_timestamp,
            value=new_value,
            updated_at=datetime.utcnow(),
        )
        await self._state_service.upsert_object(obj)
        logger.info(f"Applied MQTT command to {serial}: {fields}")

    async def on_device_state_change(self, change: DeviceStateChange) -> None:
        """Handle device state change by publishing to MQTT."""
        if not self._connected or not self._active_client:
            return

        object_type, serial = parse_object_key(change.object_key)

        if object_type not in ("device", "shared"):
            return

        try:
            # Publish raw state
            if self._publish_raw:
                await self._publish_raw_state(
                    self._active_client, serial, object_type, change.new_value
                )

            # Publish HA state
            if self._ha_discovery:
                await self._publish_ha_state(self._active_client, serial)
        except Exception as e:
            logger.error(f"Failed to publish state change: {e}")

    async def _publish_raw_state(
        self,
        client: aiomqtt.Client,
        serial: str,
        object_type: str,
        values: dict[str, Any],
    ) -> None:
        """Publish raw device state to MQTT."""
        prefix = self._topic_prefix

        # Publish full object
        full_topic = build_state_topic(prefix, serial, object_type)
        await client.publish(full_topic, json.dumps(values), retain=True)

        # Publish individual fields
        for field, value in values.items():
            field_topic = build_state_topic(prefix, serial, object_type, field)
            payload = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            await client.publish(field_topic, payload, retain=True)

    async def _publish_ha_state(
        self,
        client: aiomqtt.Client,
        serial: str,
    ) -> None:
        """Publish Home Assistant formatted state for a device."""
        prefix = self._topic_prefix

        device_obj = self._state_service.get_object(serial, f"device.{serial}")
        shared_obj = self._state_service.get_object(serial, f"shared.{serial}")

        if not device_obj or not shared_obj:
            logger.warning(f"Cannot publish HA state for {serial} - missing objects")
            return

        device_values = device_obj.value or {}
        shared_values = shared_obj.value or {}

        # Current temperature (from shared or device)
        current_temp = shared_values.get("current_temperature") or device_values.get(
            "current_temperature"
        )
        if current_temp is not None:
            await client.publish(
                f"{prefix}/{serial}/ha/current_temperature",
                str(current_temp),
                retain=True,
            )

        # Current humidity
        if "current_humidity" in device_values:
            await client.publish(
                f"{prefix}/{serial}/ha/current_humidity",
                str(device_values["current_humidity"]),
                retain=True,
            )

        # Target temperature
        if shared_values.get("target_temperature") is not None:
            await client.publish(
                f"{prefix}/{serial}/ha/target_temperature",
                str(shared_values["target_temperature"]),
                retain=True,
            )

        # Target temperature low/high
        if shared_values.get("target_temperature_low") is not None:
            await client.publish(
                f"{prefix}/{serial}/ha/target_temperature_low",
                str(shared_values["target_temperature_low"]),
                retain=True,
            )
        if shared_values.get("target_temperature_high") is not None:
            await client.publish(
                f"{prefix}/{serial}/ha/target_temperature_high",
                str(shared_values["target_temperature_high"]),
                retain=True,
            )

        # Mode (convert Nest mode to HA mode)
        ha_mode = nest_mode_to_ha(shared_values.get("target_temperature_type"))
        await client.publish(
            f"{prefix}/{serial}/ha/mode",
            ha_mode,
            retain=True,
        )

        # HVAC action
        action = derive_hvac_action(device_values, shared_values)
        await client.publish(
            f"{prefix}/{serial}/ha/action",
            action,
            retain=True,
        )

        # Fan mode
        fan_mode = get_fan_mode(device_values)
        await client.publish(
            f"{prefix}/{serial}/ha/fan_mode",
            fan_mode,
            retain=True,
        )

        # Preset mode
        preset = get_preset_mode(device_values, shared_values)
        await client.publish(
            f"{prefix}/{serial}/ha/preset",
            preset,
            retain=True,
        )

        # Outdoor temperature
        outdoor_temp = (
            device_values.get("outdoor_temperature")
            or shared_values.get("outside_temperature")
            or device_values.get("outside_temperature")
        )
        if outdoor_temp is not None:
            await client.publish(
                f"{prefix}/{serial}/ha/outdoor_temperature",
                str(outdoor_temp),
                retain=True,
            )

        # Occupancy
        is_away = is_device_away(device_values)
        await client.publish(
            f"{prefix}/{serial}/ha/occupancy",
            "away" if is_away else "home",
            retain=True,
        )

        # Fan running
        fan_running = is_fan_running(shared_values)
        await client.publish(
            f"{prefix}/{serial}/ha/fan_running",
            str(fan_running).lower(),
            retain=True,
        )

        # Eco active
        eco_active = is_eco_active(device_values)
        await client.publish(
            f"{prefix}/{serial}/ha/eco",
            str(eco_active).lower(),
            retain=True,
        )

        # Battery level (convert voltage to percentage)
        battery_voltage = device_values.get("battery_level")
        if battery_voltage is not None:
            try:
                battery_percent = battery_voltage_to_percent(float(battery_voltage))
                await client.publish(
                    f"{prefix}/{serial}/ha/battery",
                    str(battery_percent),
                    retain=True,
                )
            except (ValueError, TypeError):
                pass  # Skip if battery_level is not a valid number

        logger.debug(f"Published HA state for {serial}")

    async def on_device_connected(self, serial: str) -> None:
        """Handle device connected - publish availability."""
        if not self._connected or not self._active_client:
            return

        try:
            topic = build_availability_topic(self._topic_prefix, serial)
            await self._active_client.publish(topic, "online", retain=True)
            logger.debug(f"Published availability: {serial} = online")

            if self._ha_discovery:
                await self._publish_discovery(self._active_client, serial)
        except Exception as e:
            logger.error(f"Failed to publish device connected: {e}")

    async def on_device_disconnected(self, serial: str) -> None:
        """Handle device disconnected - publish unavailability."""
        if not self._connected or not self._active_client:
            return

        try:
            topic = build_availability_topic(self._topic_prefix, serial)
            await self._active_client.publish(topic, "offline", retain=True)
            logger.debug(f"Published availability: {serial} = offline")
        except Exception as e:
            logger.error(f"Failed to publish device disconnected: {e}")

    async def _publish_discovery(self, client: aiomqtt.Client, serial: str) -> None:
        """Publish Home Assistant discovery message for a device."""
        device_obj = self._state_service.get_object(serial, f"device.{serial}")
        shared_obj = self._state_service.get_object(serial, f"shared.{serial}")

        device_values = device_obj.value if device_obj else {}
        shared_values = shared_obj.value if shared_obj else {}

        configs = get_all_discovery_configs(
            serial,
            device_values,
            shared_values,
            self._topic_prefix,
            self._discovery_prefix,
        )

        for topic, payload in configs:
            await client.publish(topic, json.dumps(payload), retain=True)

        logger.info(f"Published HA discovery for {serial}")

    async def _remove_discovery(self, client: aiomqtt.Client, serial: str) -> None:
        """Remove Home Assistant discovery messages for a device."""
        topics = get_discovery_removal_topics(serial, self._discovery_prefix)

        for topic in topics:
            await client.publish(topic, "", retain=True)

        logger.info(f"Removed HA discovery for {serial}")

    async def _publish_all_discoveries(self, client: aiomqtt.Client) -> None:
        """Publish discovery messages for all known devices."""
        serials = self._state_service.get_all_serials()
        for serial in serials:
            try:
                await self._publish_discovery(client, serial)
            except Exception as e:
                logger.error(f"Failed to publish discovery for {serial}: {e}")

    async def _publish_initial_state(self, client: aiomqtt.Client) -> None:
        """Publish initial state and availability for all known devices."""
        serials = self._state_service.get_all_serials()
        logger.info(f"Publishing initial state for {len(serials)} device(s)")

        for serial in serials:
            try:
                device_obj = self._state_service.get_object(serial, f"device.{serial}")
                shared_obj = self._state_service.get_object(serial, f"shared.{serial}")

                if device_obj:
                    # Publish raw state
                    if self._publish_raw:
                        await self._publish_raw_state(client, serial, "device", device_obj.value)

                    if shared_obj and self._publish_raw:
                        await self._publish_raw_state(client, serial, "shared", shared_obj.value)

                # Publish HA state
                if self._ha_discovery:
                    await self._publish_ha_state(client, serial)

                # Publish availability
                availability_topic = build_availability_topic(self._topic_prefix, serial)
                await client.publish(availability_topic, "online", retain=True)
                logger.info(f"Published availability to {availability_topic}: online")

            except Exception as e:
                logger.error(f"Failed to publish initial state for {serial}: {e}")
