"""Home Assistant MQTT discovery message generation.

Reference: https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery

Discovery Topic Format:
<discovery_prefix>/<component>/[<node_id>/]<object_id>/config

Example:
homeassistant/climate/nest_02AA01AC/thermostat/config
"""

from typing import Any

from nolongerevil.integrations.mqtt.consts import MODE_TEMPERATURE_TOPICS
from nolongerevil.integrations.mqtt.helpers import get_device_name, nest_mode_to_ha
from nolongerevil.lib.consts import HaFanMode, HaMode, HaPreset


def build_climate_discovery_payload(
    serial: str,
    device_name: str,
    topic_prefix: str,
    shared_values: dict[str, Any],
) -> dict[str, Any]:
    """Build Home Assistant climate discovery payload.

    Always uses Celsius - HA handles display conversion based on user preferences.
    This avoids double-conversion bugs when Nest display unit changes.

    The discovery config is mode-aware:
    - heat_cool mode: high/low temperature topics (range with two setpoints)
    - heat or cool mode: single temperature topic
    - off mode: no temperature topics (can't set temperature when off)
    This ensures HA shows the correct UI controls.

    Args:
        serial: Device serial
        device_name: Human-readable device name
        topic_prefix: MQTT topic prefix
        shared_values: Shared object values (used to derive current mode)

    Returns:
        Discovery payload dictionary
    """
    # Derive mode from shared_values
    ha_mode = nest_mode_to_ha(shared_values.get("target_temperature_type"))

    payload: dict[str, Any] = {
        # Unique identifier
        "unique_id": f"nolongerevil_{serial}",
        # Device name
        "name": device_name,
        # NEW: HA 2026.4+ compliant
        "default_entity_id": f"climate.nest_{serial}",
        # Device info (groups all entities together)
        "device": {
            "identifiers": [f"nolongerevil_{serial}"],
            "name": device_name,
            "model": "Nest Thermostat",
            "manufacturer": "Google Nest",
            "sw_version": "NoLongerEvil",
        },
        # Availability topic
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        # Temperature unit - always Celsius (Nest internal format)
        # HA will convert to user's display preference automatically
        "temperature_unit": "C",
        # Precision (0.5 for Nest)
        "precision": 0.5,
        "temp_step": 0.5,
        # Current temperature
        "current_temperature_topic": f"{topic_prefix}/{serial}/ha/current_temperature",
        # Current humidity
        "current_humidity_topic": f"{topic_prefix}/{serial}/ha/current_humidity",
        # HVAC mode (heat, cool, heat_cool, off)
        "mode_command_topic": f"{topic_prefix}/{serial}/ha/mode/set",
        "mode_state_topic": f"{topic_prefix}/{serial}/ha/mode",
        "modes": HaMode.all(),
        # HVAC action (heating, cooling, idle, fan, off)
        "action_topic": f"{topic_prefix}/{serial}/ha/action",
        # Fan mode (on, auto)
        "fan_mode_command_topic": f"{topic_prefix}/{serial}/ha/fan_mode/set",
        "fan_mode_state_topic": f"{topic_prefix}/{serial}/ha/fan_mode",
        "fan_modes": HaFanMode.all(),
        # Preset modes (home, away, eco)
        "preset_mode_command_topic": f"{topic_prefix}/{serial}/ha/preset/set",
        "preset_mode_state_topic": f"{topic_prefix}/{serial}/ha/preset",
        "preset_modes": HaPreset.all(),
        # Min/max temperature in Celsius (typical Nest range)
        "min_temp": 9,
        "max_temp": 32,
        # Optimistic mode
        "optimistic": False,
        # QoS
        "qos": 1,
    }

    # Mode-specific temperature topics
    for topic in MODE_TEMPERATURE_TOPICS.get(ha_mode, ()):
        payload[f"{topic.discovery_key}_command_topic"] = (
            f"{topic_prefix}/{serial}/ha/{topic.topic_suffix}/set"
        )
        payload[f"{topic.discovery_key}_state_topic"] = (
            f"{topic_prefix}/{serial}/ha/{topic.topic_suffix}"
        )

    return payload


def build_temperature_sensor_discovery(
    serial: str, topic_prefix: str
) -> dict[str, Any]:
    """Build Home Assistant discovery payload for temperature sensor."""
    return {
        "unique_id": f"nolongerevil_{serial}_temperature",
        "name": "Temperature",
        "default_entity_id": f"sensor.nest_{serial}_temperature",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/current_temperature",
        "unit_of_measurement": "°C",
        "device_class": "temperature",
        "state_class": "measurement",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 0,
    }


def build_humidity_sensor_discovery(serial: str, topic_prefix: str) -> dict[str, Any]:
    """Build Home Assistant discovery payload for humidity sensor."""
    return {
        "unique_id": f"nolongerevil_{serial}_humidity",
        "name": "Humidity",
        "default_entity_id": f"sensor.nest_{serial}_humidity",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/current_humidity",
        "unit_of_measurement": "%",
        "device_class": "humidity",
        "state_class": "measurement",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 0,
    }


def build_outdoor_temperature_sensor_discovery(
    serial: str, topic_prefix: str
) -> dict[str, Any]:
    """Build Home Assistant discovery payload for outdoor temperature sensor."""
    return {
        "unique_id": f"nolongerevil_{serial}_outdoor_temperature",
        "name": "Outdoor Temperature",
        "default_entity_id": f"sensor.nest_{serial}_outdoor_temperature",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/outdoor_temperature",
        "unit_of_measurement": "°C",
        "device_class": "temperature",
        "state_class": "measurement",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 0,
    }


def build_occupancy_binary_sensor_discovery(
    serial: str, topic_prefix: str
) -> dict[str, Any]:
    """Build Home Assistant discovery payload for occupancy binary sensor."""
    return {
        "unique_id": f"nolongerevil_{serial}_occupancy",
        "name": "Occupancy",
        "default_entity_id": f"binary_sensor.nest_{serial}_occupancy",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/occupancy",
        "payload_on": HaPreset.HOME,
        "payload_off": HaPreset.AWAY,
        "device_class": "occupancy",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 0,
    }


def build_fan_binary_sensor_discovery(serial: str, topic_prefix: str) -> dict[str, Any]:
    """Build Home Assistant discovery payload for fan binary sensor."""
    return {
        "unique_id": f"nolongerevil_{serial}_fan",
        "name": "Fan",
        "default_entity_id": f"binary_sensor.nest_{serial}_fan",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/fan_running",
        "payload_on": "true",
        "payload_off": "false",
        "device_class": "running",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 0,
    }


def build_leaf_binary_sensor_discovery(
    serial: str, topic_prefix: str
) -> dict[str, Any]:
    """Build Home Assistant discovery payload for leaf (eco) binary sensor."""
    return {
        "unique_id": f"nolongerevil_{serial}_leaf",
        "name": "Eco Mode",
        "default_entity_id": f"binary_sensor.nest_{serial}_leaf",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/eco",
        "payload_on": "true",
        "payload_off": "false",
        "device_class": "power",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 0,
    }


def build_battery_sensor_discovery(serial: str, topic_prefix: str) -> dict[str, Any]:
    """Build Home Assistant discovery payload for battery sensor."""
    return {
        "unique_id": f"nolongerevil_{serial}_battery",
        "name": "Battery",
        "default_entity_id": f"sensor.nest_{serial}_battery",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/battery",
        "unit_of_measurement": "%",
        "device_class": "battery",
        "state_class": "measurement",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 0,
    }


def build_rssi_sensor_discovery(serial: str, topic_prefix: str) -> dict[str, Any]:
    """Build Home Assistant discovery payload for WiFi signal strength sensor."""
    return {
        "unique_id": f"nolongerevil_{serial}_rssi",
        "name": "WiFi Signal",
        "default_entity_id": f"sensor.nest_{serial}_rssi",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/rssi",
        "unit_of_measurement": "dBm",
        "device_class": "signal_strength",
        "state_class": "measurement",
        "entity_category": "diagnostic",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 0,
    }


def build_filter_replacement_binary_sensor_discovery(
    serial: str,
    topic_prefix: str,
) -> dict[str, Any]:
    """Build Home Assistant discovery payload for filter replacement needed sensor."""
    return {
        "unique_id": f"nolongerevil_{serial}_filter_replacement",
        "name": "Filter Replacement Needed",
        "default_entity_id": f"binary_sensor.nest_{serial}_filter_replacement",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/filter_replacement_needed",
        "payload_on": "true",
        "payload_off": "false",
        "device_class": "problem",
        "entity_category": "diagnostic",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 0,
    }


def build_filter_runtime_sensor_discovery(
    serial: str,
    topic_prefix: str,
) -> dict[str, Any]:
    """Build Home Assistant discovery payload for filter runtime sensor."""
    return {
        "unique_id": f"nolongerevil_{serial}_filter_runtime",
        "name": "Filter Runtime",
        "default_entity_id": f"sensor.nest_{serial}_filter_runtime",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/filter_runtime_days",
        "unit_of_measurement": "d",
        "icon": "mdi:air-filter",
        "state_class": "total_increasing",
        "entity_category": "diagnostic",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 0,
    }


def build_time_to_target_sensor_discovery(
    serial: str,
    topic_prefix: str,
) -> dict[str, Any]:
    """Build Home Assistant discovery payload for time to target sensor."""
    return {
        "unique_id": f"nolongerevil_{serial}_time_to_target",
        "name": "Time to Target",
        "default_entity_id": f"sensor.nest_{serial}_time_to_target",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/time_to_target",
        "unit_of_measurement": "min",
        "icon": "mdi:clock-outline",
        "state_class": "measurement",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 0,
    }


def build_sunlight_correction_binary_sensor_discovery(
    serial: str,
    topic_prefix: str,
) -> dict[str, Any]:
    """Build Home Assistant discovery payload for sunlight correction active sensor."""
    return {
        "unique_id": f"nolongerevil_{serial}_sunlight_correction",
        "name": "Sunlight Correction Active",
        "default_entity_id": f"binary_sensor.nest_{serial}_sunlight_correction",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/sunlight_correction_active",
        "payload_on": "true",
        "payload_off": "false",
        "icon": "mdi:weather-sunny",
        "entity_category": "diagnostic",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 0,
    }


def build_compressor_lockout_sensor_discovery(
    serial: str, topic_prefix: str
) -> dict[str, Any]:
    """Build Home Assistant discovery payload for compressor lockout sensor."""
    return {
        "unique_id": f"nolongerevil_{serial}_compressor_lockout",
        "name": "Compressor Lockout",
        "default_entity_id": f"sensor.nest_{serial}_compressor_lockout",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/compressor_lockout_timeout",
        "unit_of_measurement": "s",
        "icon": "mdi:timer-lock",
        "state_class": "measurement",
        "entity_category": "diagnostic",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 0,
    }


def build_learning_mode_binary_sensor_discovery(
    serial: str, topic_prefix: str
) -> dict[str, Any]:
    """Build Home Assistant discovery payload for learning mode sensor."""
    return {
        "unique_id": f"nolongerevil_{serial}_learning_mode",
        "name": "Learning Mode",
        "default_entity_id": f"binary_sensor.nest_{serial}_learning_mode",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/learning_mode",
        "payload_on": "true",
        "payload_off": "false",
        "icon": "mdi:school",
        "entity_category": "diagnostic",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 0,
    }


def build_heat_pump_ready_binary_sensor_discovery(
    serial: str,
    topic_prefix: str,
) -> dict[str, Any]:
    """Build Home Assistant discovery payload for heat pump ready sensor."""
    return {
        "unique_id": f"nolongerevil_{serial}_heat_pump_ready",
        "name": "Heat Pump Ready",
        "default_entity_id": f"binary_sensor.nest_{serial}_heat_pump_ready",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/heat_pump_ready",
        "payload_on": "true",
        "payload_off": "false",
        "icon": "mdi:heat-pump",
        "entity_category": "diagnostic",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 0,
    }


def build_local_ip_sensor_discovery(serial: str, topic_prefix: str) -> dict[str, Any]:
    """Build Home Assistant discovery payload for local IP sensor."""
    return {
        "unique_id": f"nolongerevil_{serial}_local_ip",
        "name": "Local IP",
        "default_entity_id": f"sensor.nest_{serial}_local_ip",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/local_ip",
        "icon": "mdi:ip-network",
        "entity_category": "diagnostic",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 0,
    }


def build_fan_timer_remaining_sensor_discovery(
    serial: str, topic_prefix: str
) -> dict[str, Any]:
    """Build Home Assistant discovery payload for fan timer remaining sensor."""
    return {
        "unique_id": f"nolongerevil_{serial}_fan_timer_remaining",
        "name": "Fan Timer Remaining",
        "default_entity_id": f"sensor.nest_{serial}_fan_timer_remaining",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/fan_timer_remaining",
        "unit_of_measurement": "min",
        "icon": "mdi:fan-clock",
        "state_class": "measurement",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 0,
    }


def build_fan_duration_number_discovery(
    serial: str, topic_prefix: str
) -> dict[str, Any]:
    """Build Home Assistant discovery payload for fan duration number entity."""
    return {
        "unique_id": f"nolongerevil_{serial}_fan_duration",
        "name": "Fan Duration",
        "default_entity_id": f"number.nest_{serial}_fan_duration",
        "device": {"identifiers": [f"nolongerevil_{serial}"]},
        "state_topic": f"{topic_prefix}/{serial}/ha/fan_duration",
        "command_topic": f"{topic_prefix}/{serial}/ha/fan_duration/set",
        "unit_of_measurement": "min",
        "icon": "mdi:fan-clock",
        "min": 15,
        "max": 1440,
        "step": 15,
        "mode": "slider",
        "availability": {
            "topic": f"{topic_prefix}/{serial}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        "qos": 1,
    }


def get_all_discovery_configs(
    serial: str,
    device_values: dict[str, Any],
    shared_values: dict[str, Any],
    topic_prefix: str,
    discovery_prefix: str = "homeassistant",
) -> list[tuple[str, dict[str, Any]]]:
    """Get all discovery configurations for a thermostat.

    Args:
        serial: Device serial
        device_values: Device object values
        shared_values: Shared object values
        topic_prefix: MQTT topic prefix
        discovery_prefix: HA discovery prefix (default: homeassistant)

    Returns:
        List of (topic, payload) tuples for all entities
    """
    device_name = get_device_name(device_values, shared_values, serial)
    configs = []

    # Climate entity (main thermostat control) - mode-aware for temperature topics
    climate_topic = f"{discovery_prefix}/climate/nest_{serial}/thermostat/config"
    climate_payload = build_climate_discovery_payload(
        serial, device_name, topic_prefix, shared_values
    )
    configs.append((climate_topic, climate_payload))

    # Temperature sensor
    temp_topic = f"{discovery_prefix}/sensor/nest_{serial}/temperature/config"
    temp_payload = build_temperature_sensor_discovery(serial, topic_prefix)
    configs.append((temp_topic, temp_payload))

    # Humidity sensor
    humidity_topic = f"{discovery_prefix}/sensor/nest_{serial}/humidity/config"
    humidity_payload = build_humidity_sensor_discovery(serial, topic_prefix)
    configs.append((humidity_topic, humidity_payload))

    # Outdoor temperature sensor
    outdoor_temp_topic = (
        f"{discovery_prefix}/sensor/nest_{serial}/outdoor_temperature/config"
    )
    outdoor_temp_payload = build_outdoor_temperature_sensor_discovery(
        serial, topic_prefix
    )
    configs.append((outdoor_temp_topic, outdoor_temp_payload))

    # Occupancy binary sensor
    occupancy_topic = f"{discovery_prefix}/binary_sensor/nest_{serial}/occupancy/config"
    occupancy_payload = build_occupancy_binary_sensor_discovery(serial, topic_prefix)
    configs.append((occupancy_topic, occupancy_payload))

    # Fan binary sensor
    fan_topic = f"{discovery_prefix}/binary_sensor/nest_{serial}/fan/config"
    fan_payload = build_fan_binary_sensor_discovery(serial, topic_prefix)
    configs.append((fan_topic, fan_payload))

    # Leaf (eco) binary sensor
    leaf_topic = f"{discovery_prefix}/binary_sensor/nest_{serial}/leaf/config"
    leaf_payload = build_leaf_binary_sensor_discovery(serial, topic_prefix)
    configs.append((leaf_topic, leaf_payload))

    # Battery sensor
    battery_topic = f"{discovery_prefix}/sensor/nest_{serial}/battery/config"
    battery_payload = build_battery_sensor_discovery(serial, topic_prefix)
    configs.append((battery_topic, battery_payload))

    # RSSI (WiFi signal strength) sensor
    rssi_topic = f"{discovery_prefix}/sensor/nest_{serial}/rssi/config"
    rssi_payload = build_rssi_sensor_discovery(serial, topic_prefix)
    configs.append((rssi_topic, rssi_payload))

    # Filter replacement needed binary sensor
    filter_replacement_topic = (
        f"{discovery_prefix}/binary_sensor/nest_{serial}/filter_replacement/config"
    )
    filter_replacement_payload = build_filter_replacement_binary_sensor_discovery(
        serial, topic_prefix
    )
    configs.append((filter_replacement_topic, filter_replacement_payload))

    # Filter runtime sensor
    filter_runtime_topic = (
        f"{discovery_prefix}/sensor/nest_{serial}/filter_runtime/config"
    )
    filter_runtime_payload = build_filter_runtime_sensor_discovery(serial, topic_prefix)
    configs.append((filter_runtime_topic, filter_runtime_payload))

    # Time to target sensor
    time_to_target_topic = (
        f"{discovery_prefix}/sensor/nest_{serial}/time_to_target/config"
    )
    time_to_target_payload = build_time_to_target_sensor_discovery(serial, topic_prefix)
    configs.append((time_to_target_topic, time_to_target_payload))

    # Sunlight correction active binary sensor
    sunlight_topic = (
        f"{discovery_prefix}/binary_sensor/nest_{serial}/sunlight_correction/config"
    )
    sunlight_payload = build_sunlight_correction_binary_sensor_discovery(
        serial, topic_prefix
    )
    configs.append((sunlight_topic, sunlight_payload))

    # Compressor lockout sensor
    compressor_lockout_topic = (
        f"{discovery_prefix}/sensor/nest_{serial}/compressor_lockout/config"
    )
    compressor_lockout_payload = build_compressor_lockout_sensor_discovery(
        serial, topic_prefix
    )
    configs.append((compressor_lockout_topic, compressor_lockout_payload))

    # Learning mode binary sensor
    learning_mode_topic = (
        f"{discovery_prefix}/binary_sensor/nest_{serial}/learning_mode/config"
    )
    learning_mode_payload = build_learning_mode_binary_sensor_discovery(
        serial, topic_prefix
    )
    configs.append((learning_mode_topic, learning_mode_payload))

    # Heat pump ready binary sensor
    heat_pump_ready_topic = (
        f"{discovery_prefix}/binary_sensor/nest_{serial}/heat_pump_ready/config"
    )
    heat_pump_ready_payload = build_heat_pump_ready_binary_sensor_discovery(
        serial, topic_prefix
    )
    configs.append((heat_pump_ready_topic, heat_pump_ready_payload))

    # Local IP sensor
    local_ip_topic = f"{discovery_prefix}/sensor/nest_{serial}/local_ip/config"
    local_ip_payload = build_local_ip_sensor_discovery(serial, topic_prefix)
    configs.append((local_ip_topic, local_ip_payload))

    # Fan timer remaining sensor
    fan_timer_topic = (
        f"{discovery_prefix}/sensor/nest_{serial}/fan_timer_remaining/config"
    )
    fan_timer_payload = build_fan_timer_remaining_sensor_discovery(serial, topic_prefix)
    configs.append((fan_timer_topic, fan_timer_payload))

    # Fan duration number entity
    fan_duration_topic = f"{discovery_prefix}/number/nest_{serial}/fan_duration/config"
    fan_duration_payload = build_fan_duration_number_discovery(serial, topic_prefix)
    configs.append((fan_duration_topic, fan_duration_payload))

    return configs


def get_discovery_removal_topics(
    serial: str,
    discovery_prefix: str = "homeassistant",
) -> list[str]:
    """Get all discovery topics for removing a device.

    Args:
        serial: Device serial
        discovery_prefix: HA discovery prefix

    Returns:
        List of discovery topics to clear
    """
    return [
        f"{discovery_prefix}/climate/nest_{serial}/thermostat/config",
        f"{discovery_prefix}/sensor/nest_{serial}/temperature/config",
        f"{discovery_prefix}/sensor/nest_{serial}/humidity/config",
        f"{discovery_prefix}/sensor/nest_{serial}/outdoor_temperature/config",
        f"{discovery_prefix}/sensor/nest_{serial}/battery/config",
        f"{discovery_prefix}/binary_sensor/nest_{serial}/occupancy/config",
        f"{discovery_prefix}/binary_sensor/nest_{serial}/fan/config",
        f"{discovery_prefix}/binary_sensor/nest_{serial}/leaf/config",
        f"{discovery_prefix}/sensor/nest_{serial}/rssi/config",
        f"{discovery_prefix}/binary_sensor/nest_{serial}/filter_replacement/config",
        f"{discovery_prefix}/sensor/nest_{serial}/filter_runtime/config",
        f"{discovery_prefix}/sensor/nest_{serial}/time_to_target/config",
        f"{discovery_prefix}/binary_sensor/nest_{serial}/sunlight_correction/config",
        f"{discovery_prefix}/sensor/nest_{serial}/compressor_lockout/config",
        f"{discovery_prefix}/binary_sensor/nest_{serial}/learning_mode/config",
        f"{discovery_prefix}/binary_sensor/nest_{serial}/heat_pump_ready/config",
        f"{discovery_prefix}/sensor/nest_{serial}/local_ip/config",
        f"{discovery_prefix}/sensor/nest_{serial}/fan_timer_remaining/config",
        f"{discovery_prefix}/number/nest_{serial}/fan_duration/config",
    ]
