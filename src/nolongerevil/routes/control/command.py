"""Control API command endpoint - send commands to thermostat."""

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

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
        values = {}
        if "high" in value:
            values["target_temperature_high"] = value["high"]
        if "low" in value:
            values["target_temperature_low"] = value["low"]
    else:
        # Single temperature
        values = {"target_temperature": float(value)}

    return validate_and_clamp_temperatures(values, bounds, serial)


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
    mode_map = {
        "off": "off",
        "heat": "heat",
        "cool": "cool",
        "heat-cool": "range",
        "range": "range",
        "auto": "range",
        "eco": "eco",
    }

    target_mode = mode_map.get(value.lower(), value)

    return {"target_temperature_type": target_mode}


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


# Command registry
COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "set_temperature": set_temperature,
    "set_mode": set_mode,
    "set_away": set_away,
    "set_fan": set_fan,
    "set_eco_temperatures": set_eco_temperatures,
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

        # Notify subscribers
        await subscription_manager.notify_subscribers(serial, [updated_obj])

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
