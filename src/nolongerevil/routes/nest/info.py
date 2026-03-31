"""Provisioning info endpoint - server discovery for Nest thermostats."""

import socket
from urllib.parse import urlparse

from aiohttp import web

from nolongerevil.config.environment import settings

try:
    from importlib.metadata import version as _pkg_version

    _version = _pkg_version("nolongerevil")
except Exception:
    _version = "1.0.1"


async def handle_info(_request: web.Request) -> web.Response:
    """Handle GET /info - server provisioning details.

    Returns LAN-accessible provisioning info for Nest thermostat configuration.
    No authentication required; response contains no sensitive data.

    Response fields:
        server: Always "nolongerevil"
        version: Server software version
        api_origin: Configured API origin URL
        cloudregisterurl: Value to set in /etc/nestlabs/client.config on the thermostat
        ip: Hostname/IP extracted from api_origin
        port: Port extracted from api_origin
        ssl: Whether the origin uses HTTPS
        require_device_pairing: Whether entry key is required to connect
        entry_key_ttl_seconds: How long entry keys remain valid
    """
    parsed = urlparse(settings.api_origin)
    ssl = parsed.scheme == "https"
    port = parsed.port or (443 if ssl else 80)
    hostname = parsed.hostname or ""
    try:
        socket.inet_aton(hostname)
        ip = hostname
    except OSError:
        try:
            ip = socket.gethostbyname(hostname)
        except socket.gaierror:
            ip = hostname

    return web.json_response(
        {
            "server": "nolongerevil",
            "version": _version,
            "api_origin": settings.api_origin,
            "cloudregisterurl": f"{settings.api_origin}/entry",
            "ip": ip,
            "port": port,
            "ssl": ssl,
            "require_device_pairing": settings.require_device_pairing,
            "entry_key_ttl_seconds": settings.entry_key_ttl_seconds,
        }
    )


def create_info_routes(app: web.Application) -> None:
    """Register info routes.

    Args:
        app: aiohttp application
    """
    app.router.add_get("/info", handle_info)
