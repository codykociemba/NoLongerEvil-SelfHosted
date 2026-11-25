"""Login check for the control panel and control API (CONTROL_AUTH=true)."""

from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from aiohttp import web

from nolongerevil.config import settings
from nolongerevil.middleware.api_key_auth import extract_api_key, validate_api_key
from nolongerevil.services.control_auth import ControlAuth
from nolongerevil.services.device_state_service import DeviceStateService

COOKIE = "nle_session"
PUBLIC_PATHS = {"/login", "/setup", "/link", "/health", "/nle-icon.png", "/nle-favicon.png"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _same_origin(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    return origin is None or urlparse(origin).netloc == request.host


def create_control_auth_middleware(
    auth: ControlAuth, state_service: DeviceStateService
) -> Callable[..., Awaitable[web.StreamResponse]]:
    @web.middleware
    async def middleware(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        if request.method == "OPTIONS" or request.path in PUBLIC_PATHS:
            return await handler(request)

        # Home Assistant ingress has already signed the user in. Trust the proxy's
        # address, not the header, which any client could send.
        if (
            "X-Ingress-Path" in request.headers
            and request.remote == settings.control_trusted_ingress_ip
        ):
            return await handler(request)

        key = extract_api_key(request)
        if key:
            context = await validate_api_key(key, state_service)
            scope = "read" if request.method in SAFE_METHODS else "write"
            if context and scope in context.api_key.permissions.scopes:
                return await handler(request)
            return web.json_response({"error": "Invalid API key"}, status=401)

        session = await auth.get_session(request.cookies.get(COOKIE))
        if session:
            if request.method not in SAFE_METHODS and not _same_origin(request):
                return web.json_response({"error": "Cross-site request refused"}, status=403)
            request["control_session"] = session
            return await handler(request)

        if request.method == "GET" and "text/html" in request.headers.get("Accept", ""):
            raise web.HTTPFound("/login")
        return web.json_response({"error": "Login required"}, status=401)

    return middleware
