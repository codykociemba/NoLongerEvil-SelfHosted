"""Nest entry endpoint - service discovery."""

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from nolongerevil.config import settings
from nolongerevil.lib.logger import get_logger

logger = get_logger(__name__)


async def handle_entry(request: Request) -> JSONResponse:
    """Handle Nest service discovery request.

    Returns URLs for all Nest services that the device needs to communicate with.

    Returns:
        JSON response with service URLs
    """
    origin = settings.api_origin

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

    logger.debug(f"Entry request from {request.client.host if request.client else 'unknown'}")

    return JSONResponse(response_data)


def create_entry_routes() -> list[Route]:
    """Create entry routes.

    Returns:
        List of Starlette routes
    """
    # Handle both GET and POST for /nest/entry - devices may use either
    return [
        Route("/nest/entry", handle_entry, methods=["GET", "POST"]),
    ]
