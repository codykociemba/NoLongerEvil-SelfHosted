"""API key authentication middleware."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse

from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import APIKey, DeviceSharePermission

if TYPE_CHECKING:
    from nolongerevil.services.device_state_service import DeviceStateService

logger = get_logger(__name__)


@dataclass
class APIKeyContext:
    """Context information for authenticated API requests."""

    api_key: APIKey
    user_id: str


def hash_api_key(key: str) -> str:
    """Hash an API key for lookup.

    Args:
        key: Raw API key (e.g., "nlapi_xxx")

    Returns:
        SHA-256 hash of the key
    """
    return hashlib.sha256(key.encode()).hexdigest()


def extract_api_key(request: Request) -> str | None:
    """Extract API key from request headers.

    Supports:
    - Bearer token: Authorization: Bearer nlapi_xxx
    - Direct header: X-API-Key: nlapi_xxx

    Args:
        request: Starlette request object

    Returns:
        Extracted API key or None
    """
    # Try Authorization header (Bearer token)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token.startswith("nlapi_"):
            return token

    # Try X-API-Key header
    api_key = request.headers.get("X-API-Key", "")
    if api_key.startswith("nlapi_"):
        return api_key

    return None


async def validate_api_key(
    key: str,
    state_service: "DeviceStateService",
) -> APIKeyContext | None:
    """Validate an API key and return context.

    Args:
        key: Raw API key
        state_service: Device state service for key lookup

    Returns:
        API key context or None if invalid
    """
    key_hash = hash_api_key(key)
    api_key = await state_service.storage.get_api_key_by_hash(key_hash)

    if not api_key:
        logger.debug(f"API key not found: {key[:12]}...")
        return None

    # Check expiration
    if api_key.expires_at and api_key.expires_at < datetime.now():
        logger.debug(f"API key expired: {api_key.key_preview}")
        return None

    # Update last used timestamp
    await state_service.storage.update_api_key_last_used(api_key.id)

    return APIKeyContext(
        api_key=api_key,
        user_id=api_key.user_id,
    )


async def check_device_permission(
    context: APIKeyContext,
    serial: str,
    required_scope: str,
    state_service: "DeviceStateService",
) -> bool:
    """Check if API key has permission for a device operation.

    Args:
        context: Authenticated API key context
        serial: Device serial number
        required_scope: Required scope (e.g., "read", "write")
        state_service: Device state service for ownership/sharing lookup

    Returns:
        True if permission granted
    """
    permissions = context.api_key.permissions

    # Check scope permission
    if required_scope not in permissions.scopes:
        logger.debug(f"API key {context.api_key.key_preview} lacks scope: {required_scope}")
        return False

    # Check device permission
    if permissions.devices:
        # Explicit device list
        if serial not in permissions.devices:
            logger.debug(
                f"API key {context.api_key.key_preview} not authorized for device: {serial}"
            )
            return False
    else:
        # No device restriction on key, check user ownership/sharing
        # Check if user owns the device
        owner = await state_service.storage.get_device_owner(serial)
        if owner and owner.user_id == context.user_id:
            return True

        # Check if device is shared with user
        shares = await state_service.storage.get_user_shared_devices(context.user_id)
        for share in shares:
            if share.serial == serial:
                # Check share permission level
                if required_scope == "read":
                    return True
                if required_scope == "write" and share.permissions in [
                    DeviceSharePermission.WRITE,
                    DeviceSharePermission.ADMIN,
                ]:
                    return True
                if required_scope == "admin" and share.permissions == DeviceSharePermission.ADMIN:
                    return True

        logger.debug(f"User {context.user_id} has no access to device: {serial}")
        return False

    return True


async def require_api_key_handler(
    request: Request,
    state_service: "DeviceStateService",
    required_scope: str = "read",
) -> tuple[APIKeyContext | None, JSONResponse | None]:
    """Check API key authentication for a request.

    Args:
        request: Starlette request object
        state_service: Device state service
        required_scope: Required scope for the endpoint

    Returns:
        Tuple of (context, error_response). If authentication succeeds,
        context is set and error_response is None. If it fails,
        context is None and error_response contains the error.
    """
    # Extract API key
    key = extract_api_key(request)
    if not key:
        return None, JSONResponse(
            {"error": "API key required"},
            status_code=401,
        )

    # Validate API key
    context = await validate_api_key(key, state_service)
    if not context:
        return None, JSONResponse(
            {"error": "Invalid or expired API key"},
            status_code=401,
        )

    # Check scope
    if required_scope not in context.api_key.permissions.scopes:
        return None, JSONResponse(
            {"error": f"Missing required scope: {required_scope}"},
            status_code=403,
        )

    return context, None


def get_api_key_context(request: Request) -> APIKeyContext | None:
    """Get API key context from request state.

    Args:
        request: Starlette request object

    Returns:
        API key context or None if not authenticated
    """
    return getattr(request.state, "api_key_context", None)
