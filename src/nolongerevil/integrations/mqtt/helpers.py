"""MQTT integration helper functions."""

import time
from typing import Any

# where_id to human-readable room name mapping
# Nest uses UUID-based where_id values
WHERE_ID_NAMES: dict[str, str] = {
    "00000000-0000-0000-0000-000100000000": "Entryway",
    "00000000-0000-0000-0000-000100000001": "Basement",
    "00000000-0000-0000-0000-000100000002": "Hallway",
    "00000000-0000-0000-0000-000100000003": "Den",
    "00000000-0000-0000-0000-000100000004": "Attic",
    "00000000-0000-0000-0000-000100000005": "Master Bedroom",
    "00000000-0000-0000-0000-000100000006": "Downstairs",
    "00000000-0000-0000-0000-000100000007": "Garage",
    "00000000-0000-0000-0000-000100000009": "Bathroom",
    "00000000-0000-0000-0000-00010000000a": "Kitchen",
    "00000000-0000-0000-0000-00010000000b": "Family Room",
    "00000000-0000-0000-0000-00010000000c": "Living Room",
    "00000000-0000-0000-0000-00010000000d": "Bedroom",
    "00000000-0000-0000-0000-00010000000e": "Office",
    "00000000-0000-0000-0000-00010000000f": "Upstairs",
    "00000000-0000-0000-0000-000100000010": "Dining Room",
    "00000000-0000-0000-0000-000100000011": "Backyard",
    "00000000-0000-0000-0000-000100000012": "Driveway",
    "00000000-0000-0000-0000-000100000013": "Front Yard",
    "00000000-0000-0000-0000-000100000014": "Outside",
    "00000000-0000-0000-0000-000100000015": "Guest House",
    "00000000-0000-0000-0000-000100000016": "Shed",
    "00000000-0000-0000-0000-000100000017": "Deck",
    "00000000-0000-0000-0000-000100000018": "Patio",
    "00000000-0000-0000-0000-00010000001a": "Guest Room",
    "00000000-0000-0000-0000-00010000001b": "Front Door",
    "00000000-0000-0000-0000-00010000001c": "Side Door",
    "00000000-0000-0000-0000-00010000001d": "Back Door",
}


def get_device_name(
    device_values: dict[str, Any], shared_values: dict[str, Any], serial: str
) -> str:
    """Get human-readable device name.

    Args:
        device_values: Device object values
        shared_values: Shared object values
        serial: Device serial as fallback

    Returns:
        Device name
    """
    # Try label first (user-set name)
    if shared_values.get("label"):
        return str(shared_values["label"])

    # Try name field
    if shared_values.get("name"):
        return str(shared_values["name"])

    # Try where_id (room name) - with lookup
    where_id = device_values.get("where_id")
    if where_id and isinstance(where_id, str) and where_id in WHERE_ID_NAMES:
        return WHERE_ID_NAMES[where_id]

    # Fallback to serial
    return serial


def celsius_to_fahrenheit(celsius: float) -> float:
    """Convert Celsius to Fahrenheit."""
    return (celsius * 9 / 5) + 32


def fahrenheit_to_celsius(fahrenheit: float) -> float:
    """Convert Fahrenheit to Celsius."""
    return (fahrenheit - 32) * 5 / 9


def nest_mode_to_ha(nest_mode: str | None) -> str:
    """Convert Nest mode to Home Assistant mode.

    Args:
        nest_mode: Nest temperature type

    Returns:
        Home Assistant HVAC mode
    """
    if not nest_mode:
        return "off"

    mode_map = {
        "off": "off",
        "heat": "heat",
        "cool": "cool",
        "range": "heat_cool",
        "heat-cool": "heat_cool",
    }
    return mode_map.get(nest_mode, "off")


def ha_mode_to_nest(ha_mode: str | None) -> str:
    """Convert Home Assistant mode to Nest mode.

    Args:
        ha_mode: Home Assistant HVAC mode

    Returns:
        Nest temperature type
    """
    if not ha_mode:
        return "off"

    mode_map = {
        "off": "off",
        "heat": "heat",
        "cool": "cool",
        "heat_cool": "range",
    }
    return mode_map.get(ha_mode, "off")


def derive_hvac_action(device_values: dict[str, Any], shared_values: dict[str, Any]) -> str:
    """Derive current HVAC action from device state.

    IMPORTANT: HVAC state fields (hvac_heater_state, hvac_ac_state, etc.)
    are in the SHARED object, not the device object!

    Args:
        device_values: Device object values
        shared_values: Shared object values

    Returns:
        HVAC action ("heating", "cooling", "fan", "idle", "off")
    """
    # Mode comes from shared object
    mode = shared_values.get("target_temperature_type", "off")

    if mode == "off":
        return "off"

    # Check heating states (from shared object)
    is_heating = (
        shared_values.get("hvac_heater_state")
        or shared_values.get("hvac_heat_x2_state")
        or shared_values.get("hvac_heat_x3_state")
        or shared_values.get("hvac_aux_heater_state")
        or shared_values.get("hvac_alt_heat_state")
    )

    if is_heating:
        return "heating"

    # Check cooling states (from shared object)
    is_cooling = (
        shared_values.get("hvac_ac_state")
        or shared_values.get("hvac_cool_x2_state")
        or shared_values.get("hvac_cool_x3_state")
    )

    if is_cooling:
        return "cooling"

    # Check fan running (use commanded state, not physical state)
    now_seconds = int(time.time())
    fan_timeout = device_values.get("fan_timer_timeout", 0)
    has_fan_timer = isinstance(fan_timeout, (int, float)) and fan_timeout > now_seconds
    is_fan_running = has_fan_timer or device_values.get("fan_control_state")

    if is_fan_running:
        return "fan"

    return "idle"


def get_fan_mode(device_values: dict[str, Any]) -> str:
    """Get current fan mode.

    We prioritize the commanded state (fan_timer_timeout, fan_control_state)
    over the physical state (hvac_fan_state) because the thermostat may lag
    behind server commands by up to 2 minutes due to battery-saving delays.

    Args:
        device_values: Device object values

    Returns:
        Fan mode ("auto" or "on")
    """
    now_seconds = int(time.time())
    fan_timeout = device_values.get("fan_timer_timeout", 0)
    has_fan_timer = isinstance(fan_timeout, (int, float)) and fan_timeout > now_seconds

    is_fan_on = has_fan_timer or device_values.get("fan_control_state")

    return "on" if is_fan_on else "auto"


def get_preset_mode(device_values: dict[str, Any], shared_values: dict[str, Any]) -> str:
    """Get current preset mode.

    Away mode is checked from device object (auto_away or away field).
    Eco mode is checked from device.eco.leaf or device.leaf.

    Args:
        device_values: Device object values
        shared_values: Shared object values (unused, kept for API compatibility)

    Returns:
        Preset mode ("away", "eco", "home")
    """
    # Check away mode from DEVICE object (not shared!)
    auto_away = device_values.get("auto_away")
    if isinstance(auto_away, (int, float)) and auto_away > 0:
        return "away"

    if device_values.get("away"):
        return "away"

    # Check eco mode
    eco = device_values.get("eco", {})
    if isinstance(eco, dict) and eco.get("leaf"):
        return "eco"

    if device_values.get("leaf"):
        return "eco"

    return "home"


def format_temperature(temp: float | None, precision: int = 1) -> str | None:
    """Format temperature for MQTT publishing.

    Args:
        temp: Temperature value
        precision: Decimal places

    Returns:
        Formatted temperature string or None
    """
    if temp is None:
        return None
    return f"{temp:.{precision}f}"


def battery_voltage_to_percent(voltage: float) -> int:
    """Convert Nest battery voltage to percentage.

    Nest thermostats report battery_level as voltage (typically 3.5-4.0V).
    This converts to a percentage for Home Assistant.

    Args:
        voltage: Battery voltage (typically 3.5-4.0V for Nest)

    Returns:
        Battery percentage (0-100)
    """
    # Nest thermostat battery voltage range
    # Full: ~3.9-4.0V, Empty: ~3.5V
    min_voltage = 3.5
    max_voltage = 4.0

    if voltage >= max_voltage:
        return 100
    if voltage <= min_voltage:
        return 0

    percent = ((voltage - min_voltage) / (max_voltage - min_voltage)) * 100
    return int(round(percent))


def is_device_away(device_values: dict[str, Any]) -> bool:
    """Check if device is in away mode.

    Args:
        device_values: Device object values

    Returns:
        True if device is in away mode
    """
    auto_away = device_values.get("auto_away")
    if isinstance(auto_away, (int, float)) and auto_away > 0:
        return True

    return bool(device_values.get("away"))


def is_fan_running(shared_values: dict[str, Any]) -> bool:
    """Check if fan is physically running.

    Args:
        shared_values: Shared object values

    Returns:
        True if fan is running
    """
    return bool(shared_values.get("hvac_fan_state"))


def is_eco_active(device_values: dict[str, Any]) -> bool:
    """Check if eco/leaf mode is active.

    Args:
        device_values: Device object values

    Returns:
        True if eco mode is active
    """
    eco = device_values.get("eco", {})
    if isinstance(eco, dict) and eco.get("leaf"):
        return True

    return bool(device_values.get("leaf"))
