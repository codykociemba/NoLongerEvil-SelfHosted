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
from nolongerevil.lib.mac_alias import (
    adopt_mac_ownership,
    is_mac_alias_trusted,
    resolve_mac_alias,
)
from nolongerevil.lib.serial_parser import (
    extract_basic_auth_password,
    extract_serial_from_basic_auth,
    extract_serial_from_request,
)
from nolongerevil.utils.open_mode_ownership import assign_open_mode_owner

logger = get_logger(__name__)

# Module-level cache of device api_keys (Basic Auth passwords).
# Re-captured on every transport request so the value stays current.
# In-memory only; repopulated within minutes of server restart as devices reconnect.
_device_api_keys: dict[str, str] = {}


def get_device_api_key(serial: str) -> str | None:
    """Return the cached api_key for a device (its Basic Auth password).

    This is the credential required as ``api_key`` when configuring the device
    via its local HTTP API (``POST /cgi-bin/api/settings``).
    """
    return _device_api_keys.get(serial)


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
        # Capture api_key from Basic Auth on every request, regardless of mode.
        # This runs before any early returns so open-mode devices are also covered.
        _auth = request.headers.get("Authorization")
        if _auth:
            _serial_from_auth = extract_serial_from_basic_auth(_auth)
            _password = extract_basic_auth_password(_auth)
            if _serial_from_auth and _password:
                _device_api_keys[_serial_from_auth] = _password

        # Open mode: treat every device as paired. A real thermostat that
        # talks to transport/upload is assigned to the built-in Home Assistant
        # user so downstream device ownership lookups work.
        if not settings.require_device_pairing:
            serial = extract_serial_from_request(request)
            storage = request.app.get("storage")
            if serial and storage:
                await assign_open_mode_owner(request, storage, request.app.get("state_service"))
                request["device_serial"] = serial
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

        # Resolve MAC-only devices (e.g. Display-2.12) to their real serial
        # before checking ownership/entry-key. These devices authenticate
        # with their MAC on every request, so without this a paired device
        # would look unknown here and get a 401 on every request after the
        # one that established the alias.
        mac_serial = serial
        serial, mac_alias = resolve_mac_alias(request, serial)

        request["device_serial"] = serial

        # Determine auth tier
        storage = request.app.get("storage")
        if not storage:
            # No storage available — can't enforce auth, pass through
            logger.warning("Storage not available — skipping device auth")
            request["device_auth_tier"] = TIER_PAIRED
            return await handler(request)

        # A MAC alias must not hand someone else's paired device to this MAC
        if mac_alias and not await is_mac_alias_trusted(request, mac_serial, serial):
            logger.warning(f"MAC {mac_serial} is aliased to {serial} owned by another user → 401")
            request["device_auth_tier"] = TIER_UNKNOWN
            return web.json_response(
                {"error": "Device not authorized. Complete pairing first."},
                status=401,
            )
        if mac_alias:
            await adopt_mac_ownership(request, mac_serial, serial)

        # A MAC-only device pairs under its MAC (entry key and owner are recorded
        # under the MAC), so check both the real serial and the MAC.
        auth_serials = [serial, mac_serial] if mac_alias else [serial]

        # Check ownership first (most common case for paired devices)
        for auth_serial in auth_serials:
            owner = await storage.get_device_owner(auth_serial)
            if owner:
                request["device_auth_tier"] = TIER_PAIRED
                return await handler(request)

        # Check for active entry key (pending pairing)
        entry_key = None
        for auth_serial in auth_serials:
            entry_key = await storage.get_entry_key_by_serial(auth_serial)
            if entry_key:
                break
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
