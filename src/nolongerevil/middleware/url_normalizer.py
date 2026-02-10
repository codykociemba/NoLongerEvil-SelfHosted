"""URL normalizer middleware for legacy Nest firmware compatibility."""

import re
from collections.abc import Awaitable, Callable

from aiohttp import web

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


@web.middleware
async def url_normalizer_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """Middleware to normalize legacy Nest URLs.

    Maps legacy endpoint patterns to the /nest prefix for
    backward compatibility with older Nest firmware.
    """
    original_path = request.path
    normalized_path = normalize_url(original_path)

    if normalized_path != original_path:
        # Re-resolve the handler for the normalized path
        # The handler passed in was resolved for the original URL
        new_url = normalized_path + ("?" + request.query_string if request.query_string else "")
        match_info = await request.app.router.resolve(request.clone(rel_url=new_url))

        if isinstance(match_info, web.UrlMappingMatchInfo):
            # Update the request's match_info and call the new handler
            request = request.clone(rel_url=new_url)
            # Set up the app context chain so request.app works in handlers
            match_info.add_app(request.app)
            match_info.freeze()
            request._match_info = match_info
            return await match_info.handler(request)
        else:
            # No match found, fall through to original handler (will 404)
            logger.warning(f"No route found for normalized path: {normalized_path}")

    return await handler(request)


_MiddlewareType = Callable[
    [web.Request, Callable[[web.Request], Awaitable[web.StreamResponse]]],
    Awaitable[web.StreamResponse],
]


def create_url_normalizer_middleware() -> _MiddlewareType:
    """Create the URL normalizer middleware.

    Returns:
        Middleware function
    """
    return url_normalizer_middleware
