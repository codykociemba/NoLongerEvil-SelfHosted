"""Control API scan endpoints — network discovery and Nest auto-configuration."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

from nolongerevil.config.environment import settings
from nolongerevil.lib.logger import get_logger

logger = get_logger(__name__)


async def probe_nest(session: aiohttp.ClientSession, ip: str, our_origin: str) -> dict | None:
    """Probe a single IP for a Nest local API endpoint on port 8080.

    Args:
        session: Active aiohttp session
        ip: IP address to probe
        our_origin: Our server's api_origin (trailing slash stripped)

    Returns:
        Device info dict if a Nest device responded, else None.
    """
    url = f"http://{ip}:8080/cgi-bin/api/settings"
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                cloud_url = data.get("cloudregisterurl", "")
                # Device may append a path (e.g. /entry) to the URL it stores;
                # compare by stripping any path so we match on origin only.
                from urllib.parse import urlparse as _up

                cloud_parsed = _up(cloud_url)
                our_parsed = _up(our_origin)
                configured = (
                    cloud_parsed.scheme == our_parsed.scheme
                    and cloud_parsed.netloc == our_parsed.netloc
                )
                return {
                    "ip": ip,
                    "device_name": data.get("device_name"),
                    "cloudregisterurl": cloud_url,
                    "configured": configured,
                }
    except Exception:
        pass
    return None


async def handle_scan_network(_request: web.Request) -> web.Response:
    """Handle POST /api/scan-network — scan the /24 subnet for Nest devices.

    Derives the subnet from settings.api_origin and concurrently probes all
    254 hosts for the Nest local API on port 8080 (≤50 connections, 2s timeout).

    Returns:
        JSON: {devices: [{ip, device_name, cloudregisterurl, configured}], subnet}
    """
    try:
        parsed = urlparse(settings.api_origin)
        host_ip = parsed.hostname
        if not host_ip:
            return web.json_response(
                {"error": "Cannot derive subnet: api_origin has no hostname"}, status=400
            )
        try:
            ipaddress.ip_address(host_ip)
        except ValueError:
            try:
                host_ip = socket.gethostbyname(host_ip)
            except socket.gaierror:
                return web.json_response(
                    {
                        "error": f"Cannot resolve '{host_ip}' to an IP address. Set api_origin to use an IP address instead of a hostname."
                    },
                    status=400,
                )
        network = ipaddress.IPv4Network(f"{host_ip}/24", strict=False)
    except (ValueError, TypeError) as exc:
        return web.json_response({"error": f"Invalid api_origin: {exc}"}, status=400)

    our_origin = settings.api_origin.rstrip("/")
    logger.info(f"Starting Nest scan on {network}")

    # limit=0 removes pool queuing so the connect timeout applies only to the
    # actual TCP handshake — not to time spent waiting for a pool slot.
    # A /24 is 254 hosts, well within OS file-descriptor limits for a short scan.
    connector = aiohttp.TCPConnector(limit=0)
    timeout = aiohttp.ClientTimeout(connect=2, total=4)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = [probe_nest(session, str(ip), our_origin) for ip in network.hosts()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    found = [r for r in results if isinstance(r, dict)]
    logger.info(f"Scan complete: found {len(found)} device(s) on {network}")
    return web.json_response({"devices": found, "subnet": str(network)})


async def handle_configure_nest(request: web.Request) -> web.Response:
    """Handle POST /api/configure-nest — point a discovered Nest device at this server.

    Request body:
        {"ip": "192.168.1.x"}                               # first attempt (no auth)
        {"ip": "192.168.1.x", "api_key": "02AA01AC..."}     # retry with serial

    Returns:
        200: {"success": true, "device_name": "..."}
        401: {"success": false, "auth_required": true}
        4xx/5xx: {"success": false, "error": "..."}
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)

    ip = body.get("ip")
    if not ip:
        return web.json_response({"success": False, "error": "Missing ip"}, status=400)

    api_key = body.get("api_key")
    payload: dict = {"endpoint": settings.api_origin}
    if api_key:
        payload["api_key"] = str(api_key).upper().strip()

    url = f"http://{ip}:8080/cgi-bin/api/settings"
    logger.info(f"Configuring Nest at {ip} (api_key={'yes' if api_key else 'no'})")

    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(url, json=payload) as resp,
        ):
            data = await resp.json(content_type=None)
            if resp.status == 200:
                logger.info(f"Configured {ip}: {data.get('device_name')}")
                return web.json_response({"success": True, "device_name": data.get("device_name")})
            elif resp.status == 401:
                return web.json_response({"success": False, "auth_required": True}, status=401)
            else:
                return web.json_response(
                    {
                        "success": False,
                        "error": data.get("status", "Unknown error"),
                    },
                    status=resp.status,
                )
    except aiohttp.ClientError as exc:
        logger.warning(f"Failed to configure Nest at {ip}: {exc}")
        return web.json_response(
            {"success": False, "error": f"Connection failed: {exc}"}, status=502
        )


def create_scan_routes(app: web.Application) -> None:
    """Register Nest network scan and auto-configure routes."""
    app.router.add_post("/api/scan-network", handle_scan_network)
    app.router.add_post("/api/configure-nest", handle_configure_nest)
    logger.info("Scan routes registered")
