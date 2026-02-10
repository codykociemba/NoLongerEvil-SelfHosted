"""Middleware module."""

from .api_key_auth import (
    APIKeyContext,
    check_device_permission,
    extract_api_key,
    require_api_key,
    validate_api_key,
)
from .debug_logger import create_debug_logger_middleware
from .device_auth import create_device_auth_middleware
from .device_heartbeat import create_device_heartbeat_middleware
from .url_normalizer import create_url_normalizer_middleware

__all__ = [
    "APIKeyContext",
    "check_device_permission",
    "extract_api_key",
    "require_api_key",
    "validate_api_key",
    "create_debug_logger_middleware",
    "create_device_auth_middleware",
    "create_device_heartbeat_middleware",
    "create_url_normalizer_middleware",
]
