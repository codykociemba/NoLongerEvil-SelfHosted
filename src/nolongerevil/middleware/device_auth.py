"""Device authentication middleware for Nest protocol.

Three-tier auth model:
  1. PAIRED   - Device has ownership record → full access
  2. PENDING  - Device has active entry key but no owner → subscribe OK, PUT silently dropped
  3. UNKNOWN  - No entry key, no owner → only entry + passphrase allowed, transport gets 401

The entry key displayed on the thermostat screen is the opt-in mechanism.
A server admin must physically read the key and claim it before the device
gets any transport access.
"""

from collections.abc import Awaitable, Callable

from aiohttp import web

from nolongerevil.config.environment import settings
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.serial_parser import extract_serial_from_request
from nolongerevil.services.sqlmodel_service import SQLModelService

logger = get_logger(__name__)

# Auth tiers stored on request["device_auth_tier"]
TIER_PAIRED = "paired"
TIER_PENDING = "pending"
TIER_UNKNOWN = "unknown"


def create_device_auth_middleware() -> Callable[
    [web.Request, Callable[[web.Request], Awaitable[web.StreamResponse]]],
    Awaitable[web.StreamResponse],
]:
    """Create middleware that authenticates devices against the ownership database.

    Three-tier model:
      - PAIRED:  has DeviceOwner record → full access
      - PENDING: has active (unexpired, unclaimed) entry key → subscribe only
      - UNKNOWN: neither → 401 on transport endpoints

    Entry, passphrase, ping, weather, and control API endpoints are always allowed.

    Returns:
        Middleware function
    """

    @web.middleware
    async def device_auth_middleware(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        # Open mode: skip all auth checks, treat every device as paired
        if not settings.require_device_pairing:
            request["device_auth_tier"] = TIER_PAIRED
            return await handler(request)

        path = request.path.lower()

        # Gate transport POSTs (subscribe, PUT) and uploads.
        # Everything else (entry, passphrase, ping, weather, device GET, control API) passes through.
        is_gated = (
            "/nest/transport" in path and request.method == "POST"
        ) or "/nest/upload" in path
        if not is_gated:
            return await handler(request)

        # Extract device serial
        serial = extract_serial_from_request(request)
        if not serial:
            return web.json_response({"error": "Device serial required"}, status=400)

        request["device_serial"] = serial

        # Determine auth tier
        storage: SQLModelService | None = request.app.get("storage")
        if not storage:
            # No storage available — can't enforce auth, pass through
            logger.warning("Storage not available — skipping device auth")
            request["device_auth_tier"] = TIER_PAIRED
            return await handler(request)

        # Check ownership first (most common case for paired devices)
        owner = await storage.get_device_owner(serial)
        if owner:
            request["device_auth_tier"] = TIER_PAIRED
            return await handler(request)

        # Check for active entry key (pending pairing)
        entry_key = await storage.get_entry_key_by_serial(serial)
        if entry_key:
            request["device_auth_tier"] = TIER_PENDING
            # Pending devices can subscribe (to receive pairing buckets)
            # but PUT is silently accepted and upload is rejected
            if "/put" in path:
                logger.debug(f"Pending device {serial}: accepting PUT without processing")
                return web.json_response({"objects": []})
            if "/nest/upload" in path:
                logger.debug(f"Pending device {serial}: rejecting upload")
                return web.json_response({"error": "Not authorized"}, status=401)

            # Allow subscribe through
            return await handler(request)

        # Unknown device — reject
        logger.info(f"Unknown device {serial}: no owner, no active entry key → 401")
        request["device_auth_tier"] = TIER_UNKNOWN
        return web.json_response(
            {"error": "Device not authorized. Complete pairing first."},
            status=401,
        )

    return device_auth_middleware
