"""URL normalizer middleware for legacy Nest firmware compatibility."""

import re
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from nolongerevil.lib.logger import get_logger

logger = get_logger(__name__)

# Legacy endpoint mappings to /nest prefix
LEGACY_MAPPINGS: list[tuple[re.Pattern[str], str]] = [
    # Entry and discovery endpoints
    (re.compile(r"^/entry/?$"), "/nest/entry"),
    (re.compile(r"^/ping/?$"), "/nest/ping"),
    (re.compile(r"^/passphrase/?$"), "/nest/passphrase"),
    # Transport endpoints
    (re.compile(r"^/czfe/(.*)$"), r"/nest/transport/\1"),
    (re.compile(r"^/transport/?(.*)$"), r"/nest/transport/\1"),
    # Other Nest endpoints
    (re.compile(r"^/weather/(.*)$"), r"/nest/weather/\1"),
    (re.compile(r"^/upload/?$"), "/nest/upload"),
    (re.compile(r"^/pro_info/(.*)$"), r"/nest/pro_info/\1"),
]


def normalize_url(path: str) -> str:
    """Normalize a legacy URL path to the /nest prefix format.

    Args:
        path: Original request path

    Returns:
        Normalized path with /nest prefix if applicable
    """
    # Already has /nest prefix
    if path.startswith("/nest/"):
        return path

    # Try each legacy mapping
    for pattern, replacement in LEGACY_MAPPINGS:
        match = pattern.match(path)
        if match:
            normalized = pattern.sub(replacement, path)
            logger.debug(f"Normalized URL: {path} -> {normalized}")
            return normalized

    return path


class URLNormalizerMiddleware(BaseHTTPMiddleware):
    """Middleware to normalize legacy Nest URLs.

    Maps legacy endpoint patterns to the /nest prefix for
    backward compatibility with older Nest firmware.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Normalize the URL path and forward the request."""
        original_path = request.url.path
        normalized_path = normalize_url(original_path)

        if normalized_path != original_path:
            # Create new scope with normalized path
            scope = dict(request.scope)
            scope["path"] = normalized_path

            # Update raw_path if present
            if "raw_path" in scope:
                scope["raw_path"] = normalized_path.encode("utf-8")

            # Create new request with modified scope
            request = Request(scope, request.receive, request._send)

        return await call_next(request)


def create_url_normalizer_middleware() -> type[BaseHTTPMiddleware]:
    """Create the URL normalizer middleware class.

    Returns:
        Middleware class
    """
    return URLNormalizerMiddleware
