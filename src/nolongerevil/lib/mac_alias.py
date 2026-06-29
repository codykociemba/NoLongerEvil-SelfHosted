"""MAC-address alias resolution for devices that authenticate via MAC.

Some devices (e.g. Display-2.12) only identify themselves by MAC address —
their Basic Auth username is their MAC, not their Nest-style serial. The real
serial is revealed in the session ID on /subscribe (see
extract_serial_from_session) and the mapping is then cached in
request.app["mac_to_serial"] and persisted as a "mac_alias.<mac>" object so it
survives restarts.
"""

import re

from aiohttp import web

_MAC_SERIAL_PATTERN = re.compile(r"^[0-9A-F]{12}$")

# Prefix used for the persisted "mac_alias.<mac>" bookkeeping object's serial.
# These records aren't real devices and must be excluded from
# DeviceStateService.get_all_serials() (and anything that iterates it).
MAC_ALIAS_SERIAL_PREFIX = "mac_alias."


def looks_like_mac_serial(serial: str | None) -> bool:
    """Check whether `serial` looks like a 12-hex-digit MAC address."""
    return bool(serial) and bool(_MAC_SERIAL_PATTERN.match(serial))


def resolve_mac_alias(request: web.Request, serial: str) -> tuple[str, str | None]:
    """Resolve a MAC-shaped serial to the device's real serial, if known.

    Checks the in-memory request.app["mac_to_serial"] cache first, then falls
    back to the persisted "mac_alias.<mac>" object (warming the cache on hit).

    Returns:
        Tuple of (resolved_serial, mac_alias). `mac_alias` is the lowercase
        MAC if a mapping was applied, or None if `serial` isn't MAC-shaped or
        no mapping is known yet.
    """
    if not looks_like_mac_serial(serial):
        return serial, None

    mac_lower = serial.lower()
    mac_to_serial = request.app.get("mac_to_serial")
    if mac_to_serial is None:
        mac_to_serial = {}

    resolved = mac_to_serial.get(mac_lower)
    if resolved:
        return resolved, mac_lower

    state_service = request.app.get("state_service")
    if state_service:
        mapping_obj = state_service.get_object(f"{MAC_ALIAS_SERIAL_PREFIX}{mac_lower}", "mac_alias")
        if mapping_obj:
            resolved = mapping_obj.value.get("serial")
            if resolved:
                mac_to_serial[mac_lower] = resolved
                return resolved, mac_lower

    return serial, None
