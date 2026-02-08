"""Control API status endpoints - device state inspection."""

import asyncio
import json
import time
from datetime import datetime
from typing import Any

from aiohttp import web

from nolongerevil.config.environment import settings
from nolongerevil.integrations.mqtt.helpers import get_device_name
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import DeviceObject
from nolongerevil.services.device_availability import DeviceAvailability
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.sqlmodel_service import SQLModelService
from nolongerevil.services.subscription_manager import SubscriptionManager

logger = get_logger(__name__)


def format_device_status(
    serial: str,
    state_service: DeviceStateService,
    device_availability: DeviceAvailability,
) -> dict[str, Any]:
    """Format device status for API response.

    Args:
        serial: Device serial
        state_service: Device state service
        device_availability: Device availability service

    Returns:
        Device status dictionary
    """
    device_obj = state_service.get_object(serial, f"device.{serial}")
    shared_obj = state_service.get_object(serial, f"shared.{serial}")

    device_values = device_obj.value if device_obj else {}
    shared_values = shared_obj.value if shared_obj else {}

    # Extract key fields
    # Temperature and mode values may be in shared or device objects
    # (matching MQTT integration behavior)
    last_seen = device_availability.get_last_seen(serial)
    status = {
        "serial": serial,
        "is_available": device_availability.is_available(serial),
        "last_seen": last_seen.isoformat() if last_seen else None,
        "name": get_device_name(device_values, shared_values, serial),
        "current_temperature": shared_values.get("current_temperature")
        or device_values.get("current_temperature"),
        "target_temperature": shared_values.get("target_temperature")
        or device_values.get("target_temperature"),
        "target_temperature_high": shared_values.get("target_temperature_high")
        or device_values.get("target_temperature_high"),
        "target_temperature_low": shared_values.get("target_temperature_low")
        or device_values.get("target_temperature_low"),
        "humidity": device_values.get("current_humidity"),
        "target_humidity": device_values.get("target_humidity"),
        "target_humidity_enabled": bool(device_values.get("target_humidity_enabled")),
        "mode": shared_values.get("target_temperature_type")
        or device_values.get("target_temperature_type"),
        "hvac": {
            # HVAC runtime states are in the SHARED bucket, not device
            "heater": bool(shared_values.get("hvac_heater_state")),
            "heat_x2": bool(shared_values.get("hvac_heat_x2_state")),
            "heat_x3": bool(shared_values.get("hvac_heat_x3_state")),
            "ac": bool(shared_values.get("hvac_ac_state")),
            "cool_x2": bool(shared_values.get("hvac_cool_x2_state")),
            "cool_x3": bool(shared_values.get("hvac_cool_x3_state")),
            "fan": bool(shared_values.get("hvac_fan_state")),
            "aux_heat": bool(shared_values.get("hvac_aux_heater_state")),
            "emer_heat": bool(shared_values.get("hvac_emer_heat_state")),
            "alt_heat": bool(shared_values.get("hvac_alt_heat_state")),
            # These remain in the device bucket
            "humidifier": bool(device_values.get("humidifier_state")),
            "dehumidifier": bool(device_values.get("dehumidifier_state")),
            "auto_dehum": bool(device_values.get("auto_dehum_state")),
            "fan_cooling": bool(device_values.get("fan_cooling_state")),
        },
        "fan_timer_active": isinstance(device_values.get("fan_timer_timeout", 0), (int, float))
        and device_values.get("fan_timer_timeout", 0) > time.time(),
        "fan_timer_timeout": device_values.get("fan_timer_timeout", 0),
        "eco_temperatures": {
            "high": device_values.get("away_temperature_high"),
            "low": device_values.get("away_temperature_low"),
        },
        "is_online": device_values.get("is_online", False),
        "has_leaf": device_values.get("leaf", False),
        "software_version": device_values.get("current_version"),
        "temperature_scale": device_values.get("temperature_scale", "C"),
        # Capabilities (from device bucket, default True for heat/cool per Nest convention)
        "capabilities": {
            "can_heat": device_values.get("can_heat", True),
            "can_cool": device_values.get("can_cool", True),
            "has_fan": device_values.get("has_fan", False),
            "has_emer_heat": device_values.get("has_emer_heat", False),
            "has_humidifier": device_values.get("has_humidifier", False),
            "has_dehumidifier": device_values.get("has_dehumidifier", False),
        },
        # Eco mode state (device bucket)
        "eco_mode": device_values.get("eco", {}).get("mode")
        if isinstance(device_values.get("eco"), dict)
        else None,
        # Time to target (device bucket)
        "time_to_target": device_values.get("time_to_target"),
        "time_to_target_training_status": device_values.get("time_to_target_training_status"),
        # Safety (device bucket)
        "safety_state": device_values.get("safety_state"),
        "safety_temp_activating_hvac": device_values.get("safety_temp_activating_hvac"),
        # Learning and preconditioning (device bucket)
        "learning_mode": device_values.get("learning_mode"),
        "preconditioning_enabled": device_values.get("preconditioning_enabled"),
        # Backplate (device bucket)
        "backplate_temperature": device_values.get("backplate_temperature"),
    }

    # Add shared/structure info
    if shared_values:
        status["structure_id"] = shared_values.get("structure_id")
        status["away"] = shared_values.get("away", False)
        status["schedule_mode"] = shared_values.get("schedule_mode")

    return status


async def handle_status(request: web.Request) -> web.Response:
    """Handle GET /status - get device state.

    Query parameters:
        serial: Device serial (required)

    Returns:
        JSON response with device status
    """
    serial = request.query.get("serial")
    if not serial:
        return web.json_response(
            {"error": "Serial parameter required"},
            status=400,
        )

    state_service: DeviceStateService = request.app["state_service"]
    device_availability: DeviceAvailability = request.app["device_availability"]

    # Check if device exists
    objects = state_service.get_objects_by_serial(serial)
    if not objects:
        return web.json_response(
            {"error": "Device not found"},
            status=404,
        )

    status = format_device_status(serial, state_service, device_availability)

    return web.json_response(status)


async def handle_devices(request: web.Request) -> web.Response:
    """Handle GET /api/devices - list registered (paired) devices only.

    Returns:
        JSON response with list of devices and their status
    """
    state_service: DeviceStateService = request.app["state_service"]
    device_availability: DeviceAvailability = request.app["device_availability"]
    subscription_manager: SubscriptionManager = request.app["subscription_manager"]
    storage: SQLModelService | None = request.app.get("storage")

    if storage and settings.require_device_pairing:
        # Only show devices that have been claimed/registered via entry key
        serials = await storage.get_all_registered_serials()
    else:
        # Open mode or storage unavailable — show all known devices
        serials = state_service.get_all_serials()

    devices = []
    for serial in serials:
        status = format_device_status(serial, state_service, device_availability)
        status["subscription_count"] = subscription_manager.get_subscription_count(serial)
        devices.append(status)

    return web.json_response(
        {
            "devices": devices,
            "total": len(devices),
        }
    )


async def handle_schedule(request: web.Request) -> web.Response:
    """Handle GET /api/schedule - get device schedule.

    Query parameters:
        serial: Device serial (required)

    Returns:
        JSON response with schedule data
    """
    serial = request.query.get("serial")
    if not serial:
        return web.json_response(
            {"error": "Serial parameter required"},
            status=400,
        )

    state_service: DeviceStateService = request.app["state_service"]
    schedule_obj = state_service.get_object(serial, f"schedule.{serial}")

    if not schedule_obj:
        return web.json_response({"serial": serial, "schedule": None})

    return web.json_response(
        {
            "serial": serial,
            "schedule": schedule_obj.value,
            "object_revision": schedule_obj.object_revision,
            "object_timestamp": schedule_obj.object_timestamp,
        }
    )


async def handle_notify_device(request: web.Request) -> web.Response:
    """Handle POST /notify-device - manual notification trigger.

    Forces all subscribers of a device to receive current state.
    Useful for testing or forcing state refresh.

    Request body:
        {
            "serial": "DEVICE_SERIAL"
        }

    Returns:
        JSON response with notification result
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": "Invalid JSON"},
            status=400,
        )

    serial = body.get("serial")
    if not serial:
        return web.json_response(
            {"error": "Serial required"},
            status=400,
        )

    state_service: DeviceStateService = request.app["state_service"]
    subscription_manager: SubscriptionManager = request.app["subscription_manager"]

    objects = state_service.get_objects_by_serial(serial)
    if not objects:
        return web.json_response(
            {"error": "Device not found"},
            status=404,
        )

    # Notify all subscribers with current state
    notified = await subscription_manager.notify_all_subscribers(serial, objects)

    logger.info(f"Manual notification for device {serial}: {notified} subscribers notified")

    return web.json_response(
        {
            "success": True,
            "subscribers_notified": notified,
        }
    )


async def handle_stats(request: web.Request) -> web.Response:
    """Handle GET /api/stats - get server statistics.

    Returns:
        JSON response with server stats
    """
    state_service: DeviceStateService = request.app["state_service"]
    subscription_manager: SubscriptionManager = request.app["subscription_manager"]
    device_availability: DeviceAvailability = request.app["device_availability"]

    serials = state_service.get_all_serials()
    subscription_stats = subscription_manager.get_stats()
    availability_stats = device_availability.get_all_statuses()

    stats = {
        "devices": {
            "total": len(serials),
            "available": sum(1 for s in serials if device_availability.is_available(s)),
            "serials": serials,
        },
        "subscriptions": subscription_stats,
        "availability": availability_stats,
    }

    return web.json_response(stats)


async def handle_dismiss_pairing(request: web.Request) -> web.Response:
    """Handle POST /api/dismiss-pairing/{serial} - dismiss pairing dialog for a device.

    This is called after successful device registration to dismiss the "confirm-pairing"
    alert dialog on the physical thermostat.

    Path parameters:
        serial: Device serial

    Returns:
        JSON response with result
    """
    serial = request.match_info.get("serial")
    if not serial:
        return web.json_response(
            {"error": "Serial required"},
            status=400,
        )

    state_service: DeviceStateService = request.app["state_service"]
    subscription_manager: SubscriptionManager = request.app["subscription_manager"]

    # Delete the pairing alert dialog
    alert_dialog_key = f"device_alert_dialog.{serial}"
    existing_dialog = state_service.get_object(serial, alert_dialog_key)

    if existing_dialog:
        # Update the alert dialog to dismissed state (empty dialog_id).
        # Keep it with incremented revision so device knows it changed.
        dismissed_dialog = DeviceObject(
            serial=serial,
            object_key=alert_dialog_key,
            object_revision=existing_dialog.object_revision + 1,
            object_timestamp=int(time.time() * 1000),
            value={},  # Completely empty value to dismiss the dialog
            updated_at=datetime.now(),
        )

        # Save the dismissed state
        await state_service.upsert_object(dismissed_dialog)
        logger.info(
            f"Dismissed pairing dialog for {serial} (rev {dismissed_dialog.object_revision})"
        )

        # Notify all subscribers with the dismissed dialog
        await subscription_manager.notify_all_subscribers(serial, [dismissed_dialog])

        return web.json_response(
            {
                "success": True,
                "message": f"Pairing dialog dismissed for {serial}",
            }
        )
    else:
        logger.debug(f"No pairing dialog found for {serial}")
        return web.json_response(
            {
                "success": True,
                "message": f"No pairing dialog to dismiss for {serial}",
            }
        )


async def handle_delete_device(request: web.Request) -> web.Response:
    """Handle DELETE /api/device - delete a device by serial.

    Request body:
        {
            "serial": "DEVICE_SERIAL"
        }

    Returns:
        JSON response with deletion result
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": "Invalid JSON"},
            status=400,
        )

    serial = body.get("serial")
    if not serial:
        return web.json_response(
            {"error": "Serial required"},
            status=400,
        )

    state_service: DeviceStateService = request.app["state_service"]

    # Delete from state service
    deleted_count = await state_service.delete_device(serial)

    if deleted_count > 0:
        logger.info(f"Deleted {deleted_count} objects for device {serial}")
        return web.json_response(
            {
                "success": True,
                "serial": serial,
                "objects_deleted": deleted_count,
            }
        )
    else:
        return web.json_response(
            {"error": "Device not found"},
            status=404,
        )


async def handle_sse(request: web.Request) -> web.StreamResponse:
    """Handle GET /api/events - Server-Sent Events stream.

    Pushes a lightweight event whenever device state changes,
    so the UI can refresh without polling.
    """
    resp = web.StreamResponse()
    resp.headers["Content-Type"] = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    await resp.prepare(request)

    queue: asyncio.Queue = asyncio.Queue()
    integration_manager = request.app.get("integration_manager")
    if not integration_manager:
        await resp.write(b'data: {"error":"no integration manager"}\n\n')
        return resp

    async def on_change(change):
        await queue.put(change.serial)

    integration_manager.add_state_callback(on_change)
    try:
        while True:
            serial = await queue.get()
            data = json.dumps({"serial": serial})
            await resp.write(f"data: {data}\n\n".encode())
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        integration_manager.remove_state_callback(on_change)

    return resp


def create_status_routes(
    app: web.Application,
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
    device_availability: DeviceAvailability,
) -> None:
    """Register status routes.

    Args:
        app: aiohttp application
        state_service: Device state service
        subscription_manager: Subscription manager
        device_availability: Device availability service
    """
    app["state_service"] = state_service
    app["subscription_manager"] = subscription_manager
    app["device_availability"] = device_availability

    app.router.add_get("/status", handle_status)
    app.router.add_get("/api/devices", handle_devices)
    app.router.add_get("/api/schedule", handle_schedule)
    app.router.add_get("/api/events", handle_sse)
    app.router.add_post("/notify-device", handle_notify_device)
    app.router.add_get("/api/stats", handle_stats)
    app.router.add_post("/api/dismiss-pairing/{serial}", handle_dismiss_pairing)
    app.router.add_delete("/api/device", handle_delete_device)
