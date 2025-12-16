"""Control API status endpoints - device state inspection."""

import time
from datetime import datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from nolongerevil.integrations.mqtt.helpers import get_device_name
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import DeviceObject
from nolongerevil.services.device_availability import DeviceAvailability
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.subscription_manager import SubscriptionManager

logger = get_logger(__name__)


def format_device_status(
    serial: str,
    state_service: DeviceStateService,
    device_availability: DeviceAvailability,
) -> dict[str, Any]:
    """Format device status for API response."""
    device_obj = state_service.get_object(serial, f"device.{serial}")
    shared_obj = state_service.get_object(serial, f"shared.{serial}")

    device_values = device_obj.value if device_obj else {}
    shared_values = shared_obj.value if shared_obj else {}

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
        "mode": shared_values.get("target_temperature_type")
        or device_values.get("target_temperature_type"),
        "hvac_state": shared_values.get("hvac_heater_state")
        or shared_values.get("hvac_ac_state")
        or device_values.get("hvac_heater_state")
        or device_values.get("hvac_ac_state"),
        "fan_timer_active": bool(device_values.get("fan_timer_timeout", 0)),
        "eco_temperatures": {
            "high": device_values.get("eco_temperature_high"),
            "low": device_values.get("eco_temperature_low"),
        },
        "is_online": device_values.get("is_online", False),
        "has_leaf": device_values.get("leaf", False),
        "software_version": device_values.get("current_version"),
        "temperature_scale": device_values.get("temperature_scale", "C"),
    }

    if shared_values:
        status["structure_id"] = shared_values.get("structure_id")
        status["away"] = shared_values.get("away", False)

    return status


def create_status_handlers(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
    device_availability: DeviceAvailability,
):
    """Create status handlers with injected services."""

    async def handle_status(request: Request) -> JSONResponse:
        """Handle GET /status - get device state."""
        serial = request.query_params.get("serial")
        if not serial:
            return JSONResponse({"error": "Serial parameter required"}, status_code=400)

        objects = state_service.get_objects_by_serial(serial)
        if not objects:
            return JSONResponse({"error": "Device not found"}, status_code=404)

        status = format_device_status(serial, state_service, device_availability)
        return JSONResponse(status)

    async def handle_devices(request: Request) -> JSONResponse:
        """Handle GET /api/devices - list all known devices."""
        serials = state_service.get_all_serials()

        devices = []
        for serial in serials:
            status = format_device_status(serial, state_service, device_availability)
            status["subscription_count"] = subscription_manager.get_subscription_count(serial)
            devices.append(status)

        return JSONResponse({"devices": devices, "total": len(devices)})

    async def handle_notify_device(request: Request) -> JSONResponse:
        """Handle POST /notify-device - manual notification trigger."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        serial = body.get("serial")
        if not serial:
            return JSONResponse({"error": "Serial required"}, status_code=400)

        objects = state_service.get_objects_by_serial(serial)
        if not objects:
            return JSONResponse({"error": "Device not found"}, status_code=404)

        notified = await subscription_manager.notify_all_subscribers(serial, objects)
        logger.info(f"Manual notification for device {serial}: {notified} subscribers notified")

        return JSONResponse({"success": True, "subscribers_notified": notified})

    async def handle_stats(request: Request) -> JSONResponse:
        """Handle GET /api/stats - get server statistics."""
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

        return JSONResponse(stats)

    async def handle_dismiss_pairing(request: Request) -> JSONResponse:
        """Handle POST /api/dismiss-pairing/{serial} - dismiss pairing dialog."""
        serial = request.path_params.get("serial")
        if not serial:
            return JSONResponse({"error": "Serial required"}, status_code=400)

        alert_dialog_key = f"device_alert_dialog.{serial}"
        existing_dialog = state_service.get_object(serial, alert_dialog_key)

        if existing_dialog:
            dismissed_dialog = DeviceObject(
                serial=serial,
                object_key=alert_dialog_key,
                object_revision=existing_dialog.object_revision + 1,
                object_timestamp=int(time.time() * 1000),
                value={},
                updated_at=datetime.now(),
            )

            await state_service.upsert_object(dismissed_dialog)
            logger.info(
                f"Dismissed pairing dialog for {serial} (rev {dismissed_dialog.object_revision})"
            )

            await subscription_manager.notify_all_subscribers(serial, [dismissed_dialog])

            return JSONResponse(
                {
                    "success": True,
                    "message": f"Pairing dialog dismissed for {serial}",
                }
            )
        else:
            logger.debug(f"No pairing dialog found for {serial}")
            return JSONResponse(
                {
                    "success": True,
                    "message": f"No pairing dialog to dismiss for {serial}",
                }
            )

    async def handle_delete_device(request: Request) -> JSONResponse:
        """Handle DELETE /api/device - delete a device by serial."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        serial = body.get("serial")
        if not serial:
            return JSONResponse({"error": "Serial required"}, status_code=400)

        deleted_count = await state_service.delete_device(serial)

        if deleted_count > 0:
            logger.info(f"Deleted {deleted_count} objects for device {serial}")
            return JSONResponse(
                {
                    "success": True,
                    "serial": serial,
                    "objects_deleted": deleted_count,
                }
            )
        else:
            return JSONResponse({"error": "Device not found"}, status_code=404)

    return (
        handle_status,
        handle_devices,
        handle_notify_device,
        handle_stats,
        handle_dismiss_pairing,
        handle_delete_device,
    )


def create_status_routes(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
    device_availability: DeviceAvailability,
) -> list[Route]:
    """Create status routes.

    Args:
        state_service: Device state service
        subscription_manager: Subscription manager
        device_availability: Device availability service

    Returns:
        List of Starlette routes
    """
    (
        handle_status,
        handle_devices,
        handle_notify_device,
        handle_stats,
        handle_dismiss_pairing,
        handle_delete_device,
    ) = create_status_handlers(state_service, subscription_manager, device_availability)

    return [
        Route("/status", handle_status, methods=["GET"]),
        Route("/api/devices", handle_devices, methods=["GET"]),
        Route("/notify-device", handle_notify_device, methods=["POST"]),
        Route("/api/stats", handle_stats, methods=["GET"]),
        Route("/api/dismiss-pairing/{serial}", handle_dismiss_pairing, methods=["POST"]),
        Route("/api/device", handle_delete_device, methods=["DELETE"]),
    ]
