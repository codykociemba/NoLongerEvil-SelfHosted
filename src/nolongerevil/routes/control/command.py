"""Control API command endpoint - send commands to thermostat.

Commands are dispatched through execute_command(), which:
1. Looks up the handler in COMMAND_HANDLERS
2. Calls the handler to validate input and produce a value dict
3. Routes the value to the correct bucket via COMMAND_OBJECT_KEYS
4. Merges into server state (or replaces, for schedules)
5. Pushes to the device via subscription_manager.notify_all_subscribers()

Supported commands:
- set_temperature:    Target temp (single or high/low range) → shared bucket
- set_mode:           HVAC mode (off/heat/cool/heat-cool/emergency) → shared bucket
                      "eco" is rejected here; use set_away instead
- set_away:           Eco mode via manual_eco_all → structure bucket
- set_fan:            Fan timer (on/auto/duration) → device bucket
- set_eco_temperatures: Eco high/low bounds → device bucket
- set_schedule:       Full weekly schedule replacement (ver 2) → schedule bucket
- set_schedule_mode:  Schedule mode (HEAT/COOL/RANGE) → shared bucket
- set_device_setting: Generic setter for ~45 whitelisted device fields → device bucket

Bucket routing (COMMAND_OBJECT_KEYS):
- shared.{serial}:     temperature, mode, schedule_mode
- device.{serial}:     fan, eco temps, whitelisted device settings
- structure.{id}:      manual_eco_all, manual_eco_timestamp
- schedule.{serial}:   full schedule (replacement, not merge)

Temperature commands enforce safety bounds via validate_and_clamp_temperatures().
Mode commands check device capabilities (can_heat, can_cool, has_emer_heat).
"""

import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from aiohttp import web

from nolongerevil.lib.consts import API_MODE_TO_NEST, ApiMode
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import DeviceObject
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.subscription_manager import SubscriptionManager
from nolongerevil.utils.structure_assignment import derive_structure_id
from nolongerevil.utils.temperature_safety import (
    get_safety_bounds,
    validate_and_clamp_temperatures,
)

logger = get_logger(__name__)

# Type alias for command handlers
CommandHandler = Callable[
    [DeviceStateService, str, Any],
    Awaitable[dict[str, Any]],
]


# Command handlers
async def set_temperature(
    state_service: DeviceStateService,
    serial: str,
    value: Any,
) -> dict[str, Any]:
    """Set target temperature.

    Args:
        state_service: Device state service
        serial: Device serial
        value: Temperature in Celsius or dict with high/low

    Returns:
        Updated values
    """
    device_obj = state_service.get_object(serial, f"device.{serial}")
    shared_obj = state_service.get_object(serial, f"shared.{serial}")

    bounds = get_safety_bounds(
        device_obj.value if device_obj else None,
        shared_obj.value if shared_obj else None,
    )

    if isinstance(value, dict):
        # Range mode (heat-cool)
        values = {}
        if "high" in value:
            values["target_temperature_high"] = value["high"]
        if "low" in value:
            values["target_temperature_low"] = value["low"]
    else:
        # Single temperature
        values = {"target_temperature": float(value)}

    # Set target_change_pending to wake the display
    values["target_change_pending"] = True

    return validate_and_clamp_temperatures(values, bounds, serial)


async def set_mode(
    state_service: DeviceStateService,
    serial: str,
    value: str,
) -> dict[str, Any]:
    """Set HVAC mode.

    Args:
        state_service: Device state service
        serial: Device serial
        value: Mode ("off", "heat", "cool", "heat-cool", "emergency")

    Returns:
        Updated values

    Raises:
        CommandError: If mode is invalid or device lacks the capability
    """
    mode_str = value.lower() if isinstance(value, str) else str(value).lower()

    # Reject "eco" — eco is controlled via set_away (manual_eco_all in structure bucket),
    # not target_temperature_type. "eco" is not a valid target_temperature_type value.
    if mode_str == "eco":
        raise CommandError(
            "Use set_away to control eco mode. "
            "'eco' is not a valid target_temperature_type — "
            "use manual_eco_all in the structure bucket instead."
        )

    # Convert input string to ApiMode, then lookup NestMode
    try:
        api_mode = ApiMode(mode_str)
        target_mode = API_MODE_TO_NEST.get(api_mode, mode_str)
    except ValueError:
        raise CommandError(
            f"Unknown mode '{value}'. Valid modes: off, heat, cool, heat-cool, range, auto, emergency"
        )

    # Validate device capabilities
    device_obj = state_service.get_object(serial, f"device.{serial}")
    if device_obj:
        dv = device_obj.value
        if target_mode == "heat" and not dv.get("can_heat", True):
            raise CommandError("Device does not support heating (can_heat=false)")
        if target_mode == "cool" and not dv.get("can_cool", True):
            raise CommandError("Device does not support cooling (can_cool=false)")
        if target_mode == "range" and not (dv.get("can_heat", True) and dv.get("can_cool", True)):
            raise CommandError("Range mode requires both heating and cooling capability")
        if target_mode == "emergency" and not dv.get("has_emer_heat", False):
            raise CommandError("Device does not have emergency heat (has_emer_heat=false)")

    return {"target_temperature_type": target_mode}


async def set_away(
    _state_service: DeviceStateService,
    _serial: str,
    value: bool,
) -> dict[str, Any]:
    """Set away mode via manual_eco in the structure bucket.

    Uses manual_eco_all instead of the away field because the firmware's
    schedule preconditioning reverts auto-eco (triggered by away=true) but
    respects manual-eco. The manual_eco_timestamp must be within 600 seconds
    of the device clock or the firmware silently ignores the change.

    Args:
        _state_service: Device state service (unused)
        _serial: Device serial (unused)
        value: True for away/eco, False for home

    Returns:
        Updated values (for structure object)
    """
    return {
        "manual_eco_all": value,
        "manual_eco_timestamp": int(time.time()),
    }


async def set_fan(
    state_service: DeviceStateService,
    serial: str,
    value: Any,
) -> dict[str, Any]:
    """Set fan mode or timer.

    Args:
        state_service: Device state service
        serial: Device serial
        value: "on", "auto", or duration in seconds

    Returns:
        Updated values
    """
    if isinstance(value, str):
        if value.lower() == "on":
            # Use stored fan duration preference (default 60 minutes)
            device_obj = state_service.get_object(serial, f"device.{serial}")
            duration_minutes = 60  # default
            if device_obj:
                duration_minutes = device_obj.value.get("fan_timer_duration_minutes", 60)
            return {"fan_timer_timeout": int(time.time()) + (duration_minutes * 60)}
        elif value.lower() == "auto":
            # Turn off fan timer
            return {"fan_timer_timeout": 0}
    elif isinstance(value, (int, float)):
        # Set fan timer duration (value is in seconds for backwards compatibility)
        duration = int(value)
        return {"fan_timer_timeout": int(time.time()) + duration}

    return {}


async def set_eco_temperatures(
    state_service: DeviceStateService,
    serial: str,
    value: dict[str, float],
) -> dict[str, Any]:
    """Set eco mode temperatures.

    Args:
        state_service: Device state service
        serial: Device serial
        value: Dict with "high" and/or "low" temperatures

    Returns:
        Updated values
    """
    values = {}
    if "high" in value:
        values["away_temperature_high"] = float(value["high"])
    if "low" in value:
        values["away_temperature_low"] = float(value["low"])

    device_obj = state_service.get_object(serial, f"device.{serial}")
    shared_obj = state_service.get_object(serial, f"shared.{serial}")
    bounds = get_safety_bounds(
        device_obj.value if device_obj else None,
        shared_obj.value if shared_obj else None,
    )

    return validate_and_clamp_temperatures(values, bounds, serial)


VALID_SCHEDULE_MODES = {"HEAT", "COOL", "RANGE"}
TEMP_MIN_C = 4.5
TEMP_MAX_C = 32.0


def validate_schedule(value: dict[str, Any]) -> dict[str, Any]:
    """Validate a full schedule payload.

    Args:
        value: Schedule dict with ver, schedule_mode, days, etc.

    Returns:
        Validated schedule dict

    Raises:
        CommandError: If validation fails
    """
    if not isinstance(value, dict):
        raise CommandError("Schedule must be a JSON object")
    if value.get("ver") != 2:
        raise CommandError("Schedule ver must be 2")

    mode = value.get("schedule_mode") or value.get("mode")
    if not mode or mode.upper() not in VALID_SCHEDULE_MODES:
        raise CommandError(f"schedule_mode must be one of: {', '.join(VALID_SCHEDULE_MODES)}")
    mode = mode.upper()

    days = value.get("days")
    if not isinstance(days, dict):
        raise CommandError("Schedule must contain a 'days' object")

    for day_key in days:
        if day_key not in {"0", "1", "2", "3", "4", "5", "6"}:
            raise CommandError(f"Invalid day key '{day_key}' (must be '0'-'6', Monday-Sunday)")
        day_entries = days[day_key]
        # Accept both list and dict-of-dicts (device native format)
        if isinstance(day_entries, dict):
            day_entries = [day_entries[k] for k in sorted(day_entries.keys(), key=int)]
        elif not isinstance(day_entries, list):
            raise CommandError(f"Day '{day_key}' must be a list or dict of setpoints")
        for i, entry in enumerate(day_entries):
            if not isinstance(entry, dict):
                raise CommandError(f"Day '{day_key}' entry {i}: must be an object")
            if "time" not in entry:
                raise CommandError(f"Day '{day_key}' entry {i}: missing 'time'")
            t = entry["time"]
            if not isinstance(t, (int, float)) or t < 0 or t >= 86400:
                raise CommandError(f"Day '{day_key}' entry {i}: time must be 0-86399")
            if "type" not in entry:
                raise CommandError(f"Day '{day_key}' entry {i}: missing 'type'")
            entry_mode = entry["type"].upper()
            if entry_mode not in VALID_SCHEDULE_MODES:
                raise CommandError(f"Day '{day_key}' entry {i}: invalid type '{entry['type']}'")
            # Validate temperatures
            if entry_mode == "RANGE":
                for k in ("temp-min", "temp-max"):
                    if k not in entry:
                        raise CommandError(f"Day '{day_key}' entry {i}: RANGE requires '{k}'")
                    temp = float(entry[k])
                    if temp < TEMP_MIN_C or temp > TEMP_MAX_C:
                        raise CommandError(
                            f"Day '{day_key}' entry {i}: {k}={temp} outside {TEMP_MIN_C}-{TEMP_MAX_C}C"
                        )
            elif "temp" in entry:
                temp = float(entry["temp"])
                if temp < TEMP_MIN_C or temp > TEMP_MAX_C:
                    raise CommandError(
                        f"Day '{day_key}' entry {i}: temp={temp} outside {TEMP_MIN_C}-{TEMP_MAX_C}C"
                    )

    # Return canonical form
    return {
        "ver": 2,
        "name": value.get("name", ""),
        "schedule_mode": mode,
        "days": days,
    }


async def set_schedule(
    _state_service: DeviceStateService,
    _serial: str,
    value: Any,
) -> dict[str, Any]:
    """Set the full weekly schedule (complete replacement).

    Args:
        _state_service: Device state service (unused)
        _serial: Device serial (unused)
        value: Full schedule JSON

    Returns:
        Validated schedule dict
    """
    return validate_schedule(value)


async def set_schedule_mode(
    _state_service: DeviceStateService,
    _serial: str,
    value: str,
) -> dict[str, Any]:
    """Set schedule mode (HEAT/COOL/RANGE) in the shared bucket.

    Args:
        _state_service: Device state service (unused)
        _serial: Device serial (unused)
        value: Mode string

    Returns:
        Updated values
    """
    mode = value.upper() if isinstance(value, str) else str(value).upper()
    if mode not in VALID_SCHEDULE_MODES:
        raise CommandError(f"schedule_mode must be one of: {', '.join(VALID_SCHEDULE_MODES)}")
    return {"schedule_mode": mode}


# Cloud-writable device bucket fields (access mode 2).
# These can be set via the generic set_device_setting command.
DEVICE_SETTING_WHITELIST: set[str] = {
    # Safety
    "lower_safety_temp_enabled",
    "upper_safety_temp_enabled",
    "lower_safety_temp",
    "upper_safety_temp",
    # Temperature lock
    "temp_lock_on",
    "temp_lock_pin_hash",
    "temp_lock_high_temp",
    "temp_lock_low_temp",
    # Learning and preconditioning
    "learning_mode",
    "preconditioning_enabled",
    "preconditioning_active",
    # Humidity
    "target_humidity_enabled",
    "target_humidity",
    # Display
    "temperature_scale",
    "time_to_target",
    "time_to_target_training_status",
    # Sunblock
    "sunlight_correction_enabled",
    # Fan
    "fan_timer_duration_minutes",
    "fan_duty_cycle",
    "fan_duty_start_time",
    "fan_duty_end_time",
    "fan_schedule_speed",
    # Heat pump
    "heat_pump_aux_threshold_enabled",
    "heat_pump_aux_threshold",
    "heat_pump_comp_threshold_enabled",
    "heat_pump_comp_threshold",
    # Wiring / equipment
    "equipment_type",
    "heat_source",
    # Hot water (EU models)
    "hot_water_boost_time_to_end",
    "hot_water_active",
    # Filter
    "filter_reminder_enabled",
    "filter_reminder_level",
    # Locale
    "postal_code",
    "country_code",
}


async def set_device_setting(
    _state_service: DeviceStateService,
    _serial: str,
    value: Any,
) -> dict[str, Any]:
    """Set one or more cloud-writable device bucket fields.

    Args:
        _state_service: Device state service (unused)
        _serial: Device serial (unused)
        value: Dict of field_name: field_value pairs

    Returns:
        Validated field dict

    Raises:
        CommandError: If value is not a dict or contains non-writable fields
    """
    if not isinstance(value, dict):
        raise CommandError("set_device_setting value must be a JSON object of {field: value} pairs")

    rejected = set(value.keys()) - DEVICE_SETTING_WHITELIST
    if rejected:
        raise CommandError(
            f"Fields not cloud-writable: {', '.join(sorted(rejected))}. "
            f"Only device bucket mode-2 fields are accepted."
        )

    return value


# Command registry
COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "set_temperature": set_temperature,
    "set_mode": set_mode,
    "set_away": set_away,
    "set_fan": set_fan,
    "set_eco_temperatures": set_eco_temperatures,
    "set_schedule": set_schedule,
    "set_schedule_mode": set_schedule_mode,
    "set_device_setting": set_device_setting,
}

# Object key routing for each command
COMMAND_OBJECT_KEYS: dict[str, str] = {
    "set_temperature": "shared",
    "set_mode": "shared",
    "set_away": "structure",
    "set_fan": "device",
    "set_eco_temperatures": "device",
    "set_schedule": "schedule",
    "set_schedule_mode": "shared",
    "set_device_setting": "device",
}


class CommandError(Exception):
    """Raised when command execution fails."""

    pass


async def execute_command(
    state_service: "DeviceStateService",
    subscription_manager: "SubscriptionManager",
    serial: str,
    command: str,
    value: Any,
) -> dict[str, Any]:
    """Execute a thermostat command and update state.

    This is the core command execution logic shared by HTTP API and MQTT.

    Args:
        state_service: Device state service
        subscription_manager: Subscription manager for notifying devices
        serial: Device serial number
        command: Command name (e.g., "set_temperature", "set_mode")
        value: Command value

    Returns:
        Dict with "object_key" and "values" on success

    Raises:
        CommandError: If command is unknown or execution fails
    """
    handler = COMMAND_HANDLERS.get(command)
    if not handler:
        raise CommandError(f"Unknown command: {command}")

    # Execute command handler to get values
    values = await handler(state_service, serial, value)
    if not values:
        raise CommandError("No values to update")

    # Determine target object key based on command type
    key_type = COMMAND_OBJECT_KEYS.get(command, "device")
    if key_type == "structure":
        # Look up structure_id from device owner, fall back to default
        storage = state_service.storage
        structure_id = "default"
        if storage:
            owner = await storage.get_device_owner(serial)
            if owner:
                structure_id = derive_structure_id(owner.user_id)
        object_key = f"structure.{structure_id}"
    elif key_type == "schedule":
        object_key = f"schedule.{serial}"
    elif key_type == "shared":
        object_key = f"shared.{serial}"
    else:
        object_key = f"device.{serial}"

    # Update state
    existing_obj = state_service.get_object(serial, object_key)
    new_revision = (existing_obj.object_revision if existing_obj else 0) + 1

    if key_type == "schedule":
        # Schedules are always full replacements, not merges
        updated_obj = DeviceObject(
            serial=serial,
            object_key=object_key,
            object_revision=new_revision,
            object_timestamp=int(time.time() * 1000),
            value=values,
            updated_at=datetime.now(),
        )
        await state_service.upsert_object(updated_obj)
    else:
        updated_obj = await state_service.merge_object_values(
            serial=serial,
            object_key=object_key,
            values=values,
            revision=new_revision,
            timestamp=int(time.time() * 1000),
        )

    # Notify subscribers
    await subscription_manager.notify_all_subscribers(serial, [updated_obj])

    logger.info(f"Command {command} executed for device {serial}")

    return {"object_key": updated_obj.object_key, "values": values}


async def handle_command(request: web.Request) -> web.Response:
    """Handle POST /command - send command to thermostat.

    Request body:
        {
            "serial": "DEVICE_SERIAL",
            "command": "set_temperature",
            "value": 21.5
        }

    Returns:
        JSON response with command result
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"success": False, "message": "Invalid JSON"},
            status=400,
        )

    serial = body.get("serial")
    command = body.get("command")
    value = body.get("value")

    if not serial:
        return web.json_response(
            {"success": False, "message": "Serial required"},
            status=400,
        )

    if not command:
        return web.json_response(
            {"success": False, "message": "Command required"},
            status=400,
        )

    state_service: DeviceStateService = request.app["state_service"]
    subscription_manager: SubscriptionManager = request.app["subscription_manager"]

    try:
        result = await execute_command(state_service, subscription_manager, serial, command, value)
        return web.json_response({"success": True, "data": result})

    except CommandError as e:
        return web.json_response(
            {"success": False, "message": str(e)},
            status=400,
        )
    except Exception as e:
        logger.error(f"Command {command} failed for device {serial}: {e}")
        return web.json_response(
            {"success": False, "message": str(e)},
            status=500,
        )


def create_command_routes(
    app: web.Application,
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> None:
    """Register command routes.

    Args:
        app: aiohttp application
        state_service: Device state service
        subscription_manager: Subscription manager
    """
    app["state_service"] = state_service
    app["subscription_manager"] = subscription_manager

    app.router.add_post("/command", handle_command)
