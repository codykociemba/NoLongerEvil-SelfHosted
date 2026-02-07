"""Nest entry endpoint - service discovery."""

from aiohttp import web

from nolongerevil.config import settings
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.serial_parser import extract_serial_from_request

logger = get_logger(__name__)


async def handle_entry(request: web.Request) -> web.Response:
    """Handle Nest service discovery request.

    Returns URLs for all Nest services that the device needs to communicate with.

    Per spec, entry request may include:
    - reset: Reset reason (optional)
    - mac: MAC address (optional)
    - model: Device model (optional)
    - request_id: Request identifier (optional)
    - software_version: Firmware version (optional)

    Returns:
        JSON response with service URLs
    """
    serial = extract_serial_from_request(request)
    origin = settings.api_origin_with_port

    # Parse entry request fields (form-urlencoded per spec)
    entry_info = {}
    if request.content_type == "application/x-www-form-urlencoded":
        try:
            form_data = await request.post()
            entry_info = {
                "reset": form_data.get("reset"),
                "mac": form_data.get("mac"),
                "model": form_data.get("model"),
                "software_version": form_data.get("software_version"),
                "request_id": form_data.get("request_id"),
            }
            # Filter out None values for cleaner logging
            entry_info = {k: v for k, v in entry_info.items() if v is not None}
        except Exception as e:
            logger.debug(f"Could not parse entry form data: {e}")

    # Build service URLs
    response_data = {
        "czfe_url": f"{origin}/nest/transport",
        "transport_url": f"{origin}/nest/transport",
        "direct_transport_url": f"{origin}/nest/transport",
        "passphrase_url": f"{origin}/nest/passphrase",
        "ping_url": f"{origin}/nest/transport",
        "pro_info_url": f"{origin}/nest/pro_info",
        "weather_url": f"{origin}/nest/weather/v1?query=",
        "upload_url": f"{origin}/nest/upload",
        "software_update_url": "",
        "server_version": "1.0.0",
        "tier_name": "local",
    }

    if entry_info:
        logger.debug(
            f"Entry request from {serial or request.remote}: {entry_info}"
        )
    else:
        logger.debug(f"Entry request from {serial or request.remote}")

    return web.json_response(response_data)


def create_entry_routes(app: web.Application) -> None:
    """Register entry routes.

    Args:
        app: aiohttp application
    """
    # Handle both GET and POST for /nest/entry - devices may use either
    app.router.add_get("/nest/entry", handle_entry)
    app.router.add_post("/nest/entry", handle_entry)
