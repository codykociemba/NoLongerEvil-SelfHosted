"""Control API command endpoint - send commands to thermostat."""

import time
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

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
    """Set target temperature."""
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
    # Convert input string to ApiMode, then lookup NestMode
    try:
        api_mode = ApiMode(value.lower())
        target_mode = API_MODE_TO_NEST.get(api_mode, value)
    except ValueError:
        target_mode = value  # Pass through unknown values

    return {"target_temperature_type": target_mode}


async def set_away(
    _state_service: DeviceStateService,
    _serial: str,
    value: bool,
) -> dict[str, Any]:
    """Set away mode."""
    return {"away": value}


async def set_fan(
    state_service: DeviceStateService,
    serial: str,
    value: Any,
) -> dict[str, Any]:
    """Set fan mode or timer."""
    if isinstance(value, str):
        if value.lower() == "on":
            device_obj = state_service.get_object(serial, f"device.{serial}")
            duration_minutes = 60  # default
            if device_obj:
                duration_minutes = device_obj.value.get("fan_timer_duration_minutes", 60)
            return {"fan_timer_timeout": int(time.time()) + (duration_minutes * 60)}
        elif value.lower() == "auto":
            return {"fan_timer_timeout": 0}
    elif isinstance(value, (int, float)):
        duration = int(value)
        return {"fan_timer_timeout": int(time.time()) + duration}

    return {}


async def set_eco_temperatures(
    state_service: DeviceStateService,
    serial: str,
    value: dict[str, float],
) -> dict[str, Any]:
    """Set eco mode temperatures."""
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


def create_command_handler(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
):
    """Create command handler with injected services."""

    async def handle_command(request: Request) -> JSONResponse:
        """Handle POST /command - send command to thermostat."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"success": False, "message": "Invalid JSON"},
                status_code=400,
            )

        serial = body.get("serial")
        command = body.get("command")
        value = body.get("value")

        if not serial:
            return JSONResponse(
                {"success": False, "message": "Serial required"},
                status_code=400,
            )

        if not command:
            return JSONResponse(
                {"success": False, "message": "Command required"},
                status_code=400,
            )

        handler = COMMAND_HANDLERS.get(command)
        if not handler:
            return JSONResponse(
                {"success": False, "message": f"Unknown command: {command}"},
                status_code=400,
            )

        try:
            # Execute command handler
            values = await handler(state_service, serial, value)

            if not values:
                return JSONResponse(
                    {"success": False, "message": "No values to update"},
                    status_code=400,
                )

            # Determine target object key
            if command == "set_away":
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

            return JSONResponse({
                "success": True,
                "data": {
                    "object_key": updated_obj.object_key,
                    "values": values,
                },
            })

        except Exception as e:
            logger.error(f"Command {command} failed for device {serial}: {e}")
            return JSONResponse(
                {"success": False, "message": str(e)},
                status_code=500,
            )

    return handle_command


def create_command_routes(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> list[Route]:
    """Create command routes.

    Args:
        state_service: Device state service
        subscription_manager: Subscription manager

    Returns:
        List of Starlette routes
    """
    handler = create_command_handler(state_service, subscription_manager)
    return [Route("/command", handler, methods=["POST"])]
