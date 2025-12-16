"""Serial number parsing utilities for Nest devices."""

import base64
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.requests import Request

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


def extract_serial_from_custom_header(request: "Request") -> str | None:
    """Extract serial from custom X-NL-Device-Serial header.

    Args:
        request: Starlette request

    Returns:
        Sanitized serial or None if not found
    """
    serial_header = request.headers.get("x-nl-device-serial")
    if not serial_header:
        return None
    return sanitize_serial(serial_header)


def extract_serial_from_request(request: "Request") -> str | None:
    """Extract device serial from a Starlette request.

    Tries multiple sources in order:
    1. Authorization header (Basic Auth username)
    2. X-NL-Device-Serial header
    3. Query parameter 'serial'
    4. URL path parameter 'serial'

    Args:
        request: Starlette request

    Returns:
        Sanitized serial or None if not found
    """
    # Try Basic Auth first (most common for device requests)
    auth_header = request.headers.get("Authorization")
    serial = extract_serial_from_basic_auth(auth_header)
    if serial:
        return serial

    # Try custom header (X-NL-Device-Serial)
    serial = extract_serial_from_custom_header(request)
    if serial:
        return serial

    # Try query parameter
    serial = sanitize_serial(request.query_params.get("serial"))
    if serial:
        return serial

    # Try URL path parameter
    serial = sanitize_serial(request.path_params.get("serial"))
    if serial:
        return serial

    return None


def extract_weave_device_id(request: "Request") -> str | None:
    """Extract Weave device ID from request header.

    Nest devices send their Weave device ID in the x-nl-weave-device-id header.

    Args:
        request: Starlette request

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
