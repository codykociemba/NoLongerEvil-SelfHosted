"""Control API command endpoint - send commands to thermostat."""

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from nolongerevil.lib.consts import API_MODE_TO_NEST, ApiMode
from nolongerevil.lib.logger import get_logger
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.subscription_manager import SubscriptionManager
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
        values: dict[str, Any] = {}
        if "high" in value:
            values["target_temperature_high"] = float(value["high"])
        if "low" in value:
            values["target_temperature_low"] = float(value["low"])
    else:
        # Single temperature
        values = {"target_temperature": float(value)}

    # Validate and clamp
    result = validate_and_clamp_temperatures(values, bounds, serial)

    # Always set target_change_pending for temperature changes
    result["target_change_pending"] = True

    return result


async def set_mode(
    _state_service: DeviceStateService,
    _serial: str,
    value: str,
) -> dict[str, Any]:
    """Set HVAC mode.

    Args:
        _state_service: Device state service (unused)
        _serial: Device serial (unused)
        value: Mode ("off", "heat", "cool", "heat-cool", "eco")

    Returns:
        Updated values
    """
    # Convert input string to ApiMode, then lookup NestMode
    try:
        api_mode = ApiMode(value.lower())
        target_mode = API_MODE_TO_NEST.get(api_mode, value)
    except ValueError:
        target_mode = value  # Pass through unknown values

    result: dict[str, Any] = {"target_temperature_type": target_mode}

    # Clear opposing HVAC states when switching modes to prevent conflicts
    if target_mode == "heat":
        # Clear cooling states when switching to heat
        result.update(
            {
                "hvac_ac_state": False,
                "hvac_cool_x2_state": False,
                "hvac_cool_x3_state": False,
                "hvac_fan_state": False,
            }
        )
    elif target_mode == "cool":
        # Clear heating states when switching to cool
        result.update(
            {
                "hvac_heater_state": False,
                "hvac_heat_x2_state": False,
                "hvac_heat_x3_state": False,
                "hvac_aux_heater_state": False,
                "hvac_alt_heat_state": False,
                "hvac_alt_heat_x2_state": False,
                "hvac_fan_state": False,
            }
        )
    elif target_mode == "range":
        # Clear all HVAC states when switching to range - let thermostat decide
        result.update(
            {
                "hvac_ac_state": False,
                "hvac_cool_x2_state": False,
                "hvac_cool_x3_state": False,
                "hvac_heater_state": False,
                "hvac_heat_x2_state": False,
                "hvac_heat_x3_state": False,
                "hvac_aux_heater_state": False,
                "hvac_alt_heat_state": False,
                "hvac_alt_heat_x2_state": False,
                "hvac_fan_state": False,
            }
        )

    return result


async def set_away(
    _state_service: DeviceStateService,
    _serial: str,
    value: bool,
) -> dict[str, Any]:
    """Set away mode.

    Args:
        _state_service: Device state service (unused)
        _serial: Device serial (unused)
        value: True for away, False for home

    Returns:
        Updated values (for structure object)
    """
    return {"away": value}


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
        values["eco_temperature_high"] = float(value["high"])
    if "low" in value:
        values["eco_temperature_low"] = float(value["low"])

    device_obj = state_service.get_object(serial, f"device.{serial}")
    shared_obj = state_service.get_object(serial, f"shared.{serial}")
    bounds = get_safety_bounds(
        device_obj.value if device_obj else None,
        shared_obj.value if shared_obj else None,
    )

    return validate_and_clamp_temperatures(values, bounds, serial)


async def set_eco(
    _state_service: DeviceStateService,
    _serial: str,
    value: Any,
) -> dict[str, Any]:
    """Toggle eco mode on/off.

    Args:
        _state_service: Device state service (unused)
        _serial: Device serial (unused)
        value: True/"true" to enable eco mode, False/"false" to disable

    Returns:
        Updated values
    """
    eco_enabled = value is True or str(value).lower() == "true"
    now_sec = int(time.time())

    return {
        "eco": {
            "mode": "manual-eco" if eco_enabled else "schedule",
            "touched_by": 1,
            "mode_update_timestamp": now_sec,
        },
        "leaf": eco_enabled,
        "touched_by": 1,
        "touched_when": now_sec,
        "touched_tzo": -time.timezone,
        "touched_id": 1,
    }


async def set_fan_timer(
    _state_service: DeviceStateService,
    _serial: str,
    value: int,
) -> dict[str, Any]:
    """Set fan timer with specific duration.

    This command updates both shared and device objects for proper fan control.

    Args:
        _state_service: Device state service (unused)
        _serial: Device serial (unused)
        value: Duration in seconds

    Returns:
        Updated values for device object
    """
    duration = int(value)
    timeout = int(time.time()) + duration

    return {
        "fan_control_state": True,
        "fan_mode": "auto",
        "fan_timer_duration": duration,
        "fan_current_speed": "stage1",
        "fan_timer_timeout": timeout,
    }


# Command registry
COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "set_temperature": set_temperature,
    "set_mode": set_mode,
    "set_away": set_away,
    "set_fan": set_fan,
    "set_eco_temperatures": set_eco_temperatures,
    "set_eco": set_eco,
    "set_fan_timer": set_fan_timer,
}


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

    handler = COMMAND_HANDLERS.get(command)
    if not handler:
        return web.json_response(
            {"success": False, "message": f"Unknown command: {command}"},
            status=400,
        )

    state_service: DeviceStateService = request.app["state_service"]
    subscription_manager: SubscriptionManager = request.app["subscription_manager"]

    try:
        # Execute command handler
        values = await handler(state_service, serial, value)

        if not values:
            return web.json_response(
                {"success": False, "message": "No values to update"},
                status=400,
            )

        # Determine target object key
        if command == "set_away":
            # Away mode is set on structure object
            shared_obj = state_service.get_object(serial, f"shared.{serial}")
            structure_id = shared_obj.value.get("structure_id") if shared_obj else None
            object_key = f"structure.{structure_id}" if structure_id else f"shared.{serial}"
        else:
            object_key = f"device.{serial}"

        # Update state
        now = int(time.time())
        updated_obj = await state_service.merge_object_values(
            serial=serial,
            object_key=object_key,
            values=values,
            revision=now,
            timestamp=now,
        )

        updated_objects = [updated_obj]

        # Handle commands that need to update multiple objects
        if command == "set_fan_timer":
            # Fan timer also needs to update shared object with hvac_fan_state
            shared_obj = await state_service.merge_object_values(
                serial=serial,
                object_key=f"shared.{serial}",
                values={"hvac_fan_state": True},
                revision=now,
                timestamp=now,
            )
            updated_objects.append(shared_obj)

        # Notify subscribers
        await subscription_manager.notify_subscribers(serial, updated_objects)

        logger.info(f"Command {command} executed for device {serial}")

        return web.json_response(
            {
                "success": True,
                "data": {
                    "object_key": updated_obj.object_key,
                    "values": values,
                },
            }
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
