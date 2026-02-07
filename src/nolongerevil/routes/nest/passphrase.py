"""Nest passphrase endpoint - entry key generation for device pairing."""

import time
from datetime import UTC, datetime

from aiohttp import web

from nolongerevil.config import settings
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.serial_parser import extract_serial_from_request
from nolongerevil.lib.types import DeviceObject
from nolongerevil.services.device_state_service import DeviceStateService

logger = get_logger(__name__)


async def handle_passphrase_status(request: web.Request) -> web.Response:
    """Handle entry key status check - device polls this to see if pairing completed.

    The device generates an entry key, displays it to the user, then polls this
    endpoint to check if the user has claimed the key.

    Returns:
        JSON response with pairing status
    """
    # Extract device serial
    serial = extract_serial_from_request(request)
    if not serial:
        logger.warning("Passphrase status request without valid serial")
        return web.json_response(
            {"error": "Device serial required"},
            status=400,
        )

    state_service: DeviceStateService = request.app["state_service"]

    # Get the most recent entry key for this device (including claimed/expired ones)
    entry_key = await state_service.storage.get_latest_entry_key_by_serial(serial)

    if not entry_key:
        # No entry key found - device hasn't requested one yet
        logger.debug(f"No entry key found for {serial}")
        return web.json_response(
            {
                "status": "no_key",
                "claimed": False,
                "message": "No entry key found for this device",
            }
        )

    # Check if the entry key has been claimed
    if entry_key.claimed_by:
        logger.info(f"Entry key for {serial} was claimed by {entry_key.claimed_by}")
        # Convert datetime to millisecond timestamp for response
        claimed_at_ms = (
            int(entry_key.claimed_at.timestamp() * 1000) if entry_key.claimed_at else None
        )
        return web.json_response(
            {
                "status": "claimed",
                "claimed": True,
                "claimedBy": entry_key.claimed_by,
                "claimedAt": claimed_at_ms,
            }
        )
    else:
        # Entry key exists but hasn't been claimed yet
        logger.debug(f"Entry key for {serial} not yet claimed")
        # Convert datetime to millisecond timestamp for response
        expires_at_ms = int(entry_key.expires_at.timestamp() * 1000)
        return web.json_response(
            {
                "status": "pending",
                "claimed": False,
                "expiresAt": expires_at_ms,
            }
        )


async def handle_passphrase(request: web.Request) -> web.Response:
    """Handle entry key generation request.

    Returns the existing unexpired entry key if one exists, otherwise generates
    a new one. The device polls this endpoint repeatedly until pairing completes,
    so we must return the same key to avoid invalidating it before the user can
    enter it.

    Returns:
        JSON response with entry key (expires must be a NUMBER, not string)
    """
    # Extract device serial
    serial = extract_serial_from_request(request)
    if not serial:
        logger.warning("Passphrase request without valid serial")
        return web.json_response(
            {"error": "Device serial required"},
            status=400,
        )

    state_service: DeviceStateService = request.app["state_service"]
    ttl = settings.entry_key_ttl_seconds

    # Check for existing unexpired unclaimed key first
    existing_key = await state_service.storage.get_latest_entry_key_by_serial(serial)
    if existing_key and not existing_key.claimed_by and existing_key.expires_at > datetime.now(UTC):
        expires_ms = int(existing_key.expires_at.timestamp() * 1000)
        logger.debug(f"Returning existing entry key for {serial}: {existing_key.code}")
        return web.json_response(
            {
                "value": existing_key.code,
                "expires": expires_ms,  # Must be NUMBER, not string
            }
        )

    # No valid key exists, generate new one
    entry_key = await state_service.storage.generate_entry_key(serial, ttl)

    if not entry_key:
        logger.error(f"Failed to generate entry key for {serial}")
        return web.json_response(
            {"error": "Entry key service unavailable"},
            status=503,
        )

    logger.info(f"Generated entry key for {serial}: {entry_key.get('code')}")

    # Create the pairing alert dialog for the device
    # The device will subscribe to this and wait for it to be dismissed
    alert_dialog_key = f"device_alert_dialog.{serial}"
    existing_dialog = state_service.get_object(serial, alert_dialog_key)

    if not existing_dialog:
        # Create pairing confirmation dialog
        dialog_value = {"dialog_data": "", "dialog_id": "confirm-pairing"}
        pairing_dialog = DeviceObject(
            serial=serial,
            object_key=alert_dialog_key,
            object_revision=1,
            object_timestamp=int(time.time() * 1000),
            value=dialog_value,
            updated_at=datetime.now(),
        )
        await state_service.upsert_object(pairing_dialog)
        logger.info(f"Created pairing dialog for {serial}")

    return web.json_response(
        {
            "value": entry_key.get("code"),
            "expires": entry_key.get("expiresAt"),  # Must be NUMBER, not string
        }
    )


def create_passphrase_routes(
    app: web.Application,
    state_service: DeviceStateService,
) -> None:
    """Register passphrase routes.

    Args:
        app: aiohttp application
        state_service: Device state service
    """
    app["state_service"] = state_service
    app.router.add_get("/nest/passphrase", handle_passphrase)
    app.router.add_get("/nest/passphrase/status", handle_passphrase_status)
