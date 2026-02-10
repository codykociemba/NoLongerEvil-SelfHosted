"""Serial number parsing utilities for Nest devices."""

import base64
import re

from aiohttp import web

# Minimum length for a valid Nest device serial
MIN_SERIAL_LENGTH = 10


def sanitize_serial(serial: str | None) -> str | None:
    """Sanitize and validate a device serial number.

    Args:
        serial: Raw serial string

    Returns:
        Sanitized serial (uppercase, alphanumeric only) or None if invalid
    """
    if not serial:
        return None

    # Remove all non-alphanumeric characters and convert to uppercase
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", serial).upper()

    # Validate minimum length
    if len(cleaned) < MIN_SERIAL_LENGTH:
        return None

    return cleaned


def extract_serial_from_basic_auth(auth_header: str | None) -> str | None:
    """Extract device serial from HTTP Basic Auth header.

    Nest devices use the serial number as the username in Basic Auth.
    Username may be prefixed with "nest." (e.g., "nest.02AA01AB501203EQ").

    Args:
        auth_header: Authorization header value

    Returns:
        Sanitized serial or None if not found/invalid
    """
    if not auth_header:
        return None

    # Must be Basic auth
    if not auth_header.startswith("Basic "):
        return None

    try:
        # Decode base64 credentials
        encoded = auth_header[6:]  # Remove "Basic " prefix
        decoded = base64.b64decode(encoded).decode("utf-8")

        # Split username:password
        if ":" not in decoded:
            return None

        username = decoded.split(":")[0]

        # Handle nest.SERIAL prefix format
        serial = username
        if "." in serial:
            parts = serial.split(".")
            serial = parts[1] if len(parts) > 1 and parts[1] else parts[0]

        return sanitize_serial(serial)

    except (ValueError, UnicodeDecodeError):
        return None


def extract_serial_from_custom_header(request: web.Request) -> str | None:
    """Extract serial from custom X-NL-Device-Serial header.

    Args:
        request: aiohttp web request

    Returns:
        Sanitized serial or None if not found
    """
    serial_header = request.headers.get("x-nl-device-serial")
    if not serial_header:
        return None
    return sanitize_serial(serial_header)


def extract_serial_from_client_id(client_id: str | None) -> str | None:
    """Extract device serial from X-nl-client-id header.

    Devices send this header when using DEFAULT credentials (no valid session).
    Format: d.{SERIAL}.{random} (same as Basic Auth username format).

    Args:
        client_id: X-nl-client-id header value

    Returns:
        Sanitized serial or None if not found/invalid
    """
    if not client_id:
        return None

    # Format: d.{SERIAL}.{random}
    if "." in client_id:
        parts = client_id.split(".")
        serial = parts[1] if len(parts) > 1 and parts[1] else parts[0]
        return sanitize_serial(serial)

    return sanitize_serial(client_id)


def extract_serial_from_request(request: web.Request) -> str | None:
    """Extract device serial from an aiohttp request.

    Tries multiple sources in order:
    1. Authorization header (Basic Auth username)
    2. X-nl-client-id header (subscribe requests with DEFAULT creds)
    3. X-nl-device-id header (frontdoor requests with DEFAULT creds)
    4. X-NL-Device-Serial header
    5. Query parameter 'serial'
    6. URL path parameter 'serial'

    Args:
        request: aiohttp web request

    Returns:
        Sanitized serial or None if not found
    """
    # Try Basic Auth first (most common for device requests)
    auth_header = request.headers.get("Authorization")
    serial = extract_serial_from_basic_auth(auth_header)
    if serial:
        return serial

    # Try X-nl-client-id (sent by device with DEFAULT creds on subscribe)
    serial = extract_serial_from_client_id(request.headers.get("X-nl-client-id"))
    if serial:
        return serial

    # Try X-nl-device-id (sent by device with DEFAULT creds on frontdoor)
    serial = sanitize_serial(request.headers.get("X-nl-device-id"))
    if serial:
        return serial

    # Try custom header (X-NL-Device-Serial)
    serial = extract_serial_from_custom_header(request)
    if serial:
        return serial

    # Try query parameter
    serial = sanitize_serial(request.query.get("serial"))
    if serial:
        return serial

    # Try URL path parameter
    serial = sanitize_serial(request.match_info.get("serial"))
    if serial:
        return serial

    return None


def extract_weave_device_id(request: web.Request) -> str | None:
    """Extract Weave device ID from request header.

    Nest devices send their Weave device ID in the x-nl-weave-device-id header.

    Args:
        request: aiohttp web request

    Returns:
        Weave device ID or None if not found
    """
    return request.headers.get("x-nl-weave-device-id")


def is_valid_serial(serial: str | None) -> bool:
    """Check if a serial number is valid.

    Args:
        serial: Serial number to validate

    Returns:
        True if valid, False otherwise
    """
    if not serial:
        return False

    # Must be uppercase alphanumeric and meet minimum length
    if not re.match(r"^[A-Z0-9]+$", serial):
        return False

    return len(serial) >= MIN_SERIAL_LENGTH
