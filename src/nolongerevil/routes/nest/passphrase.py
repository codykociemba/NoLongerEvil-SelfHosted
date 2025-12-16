"""Nest passphrase endpoint - entry key generation for device pairing."""

import time
from datetime import datetime

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from nolongerevil.config import settings
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.serial_parser import extract_serial_from_request
from nolongerevil.lib.types import DeviceObject
from nolongerevil.services.device_state_service import DeviceStateService

logger = get_logger(__name__)


def create_passphrase_handlers(state_service: DeviceStateService):
    """Create passphrase handlers with injected service."""

    async def handle_passphrase_status(request: Request) -> JSONResponse:
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
            return JSONResponse(
                {"error": "Device serial required"},
                status_code=400,
            )

        # Get the most recent entry key for this device (including claimed/expired ones)
        entry_key = await state_service.storage.get_latest_entry_key_by_serial(serial)

        if not entry_key:
            # No entry key found - device hasn't requested one yet
            logger.debug(f"No entry key found for {serial}")
            return JSONResponse({
                "status": "no_key",
                "claimed": False,
                "message": "No entry key found for this device",
            })

        # Check if the entry key has been claimed
        if entry_key.claimed_by:
            logger.info(f"Entry key for {serial} was claimed by {entry_key.claimed_by}")
            # Convert datetime to millisecond timestamp for response
            claimed_at_ms = (
                int(entry_key.claimed_at.timestamp() * 1000) if entry_key.claimed_at else None
            )
            return JSONResponse({
                "status": "claimed",
                "claimed": True,
                "claimedBy": entry_key.claimed_by,
                "claimedAt": claimed_at_ms,
            })
        else:
            # Entry key exists but hasn't been claimed yet
            logger.debug(f"Entry key for {serial} not yet claimed")
            # Convert datetime to millisecond timestamp for response
            expires_at_ms = int(entry_key.expires_at.timestamp() * 1000)
            return JSONResponse({
                "status": "pending",
                "claimed": False,
                "expiresAt": expires_at_ms,
            })

    async def handle_passphrase(request: Request) -> JSONResponse:
        """Handle entry key generation request.

        Generates a unique pairing code for the requesting device.
        Uses deviceStateManager.generateEntryKey() to match TypeScript behavior.

        Returns:
            JSON response with entry key
        """
        # Extract device serial
        serial = extract_serial_from_request(request)
        if not serial:
            logger.warning("Passphrase request without valid serial")
            return JSONResponse(
                {"error": "Device serial required"},
                status_code=400,
            )

        ttl = settings.entry_key_ttl_seconds

        # Use generateEntryKey to handle code generation, expiration, and storage
        entry_key = await state_service.storage.generate_entry_key(serial, ttl)

        if not entry_key:
            logger.error(f"Failed to generate entry key for {serial}")
            return JSONResponse(
                {"error": "Entry key service unavailable"},
                status_code=503,
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

        return JSONResponse({
            "value": entry_key.get("code"),
            "expires": entry_key.get("expiresAt"),
        })

    return handle_passphrase, handle_passphrase_status


def create_passphrase_routes(state_service: DeviceStateService) -> list[Route]:
    """Create passphrase routes.

    Args:
        state_service: Device state service

    Returns:
        List of Starlette routes
    """
    handle_passphrase, handle_passphrase_status = create_passphrase_handlers(state_service)
    return [
        Route("/nest/passphrase", handle_passphrase, methods=["GET"]),
        Route("/nest/passphrase/status", handle_passphrase_status, methods=["GET"]),
    ]
