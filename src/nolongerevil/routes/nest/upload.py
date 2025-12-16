"""Nest upload endpoint - device log file upload."""

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.serial_parser import extract_serial_from_request

logger = get_logger(__name__)


async def handle_upload(request: Request) -> JSONResponse:
    """Handle device log file upload.

    Devices may upload diagnostic logs. We acknowledge receipt
    but don't necessarily store them unless debug logging is enabled.

    Returns:
        Success response
    """
    serial = extract_serial_from_request(request)

    # Read the upload data (but we don't store it by default)
    try:
        data = await request.body()
        size = len(data)
        logger.info(f"Received log upload from device {serial or 'unknown'}: {size} bytes")
    except Exception as e:
        logger.warning(f"Failed to read upload data: {e}")

    return JSONResponse({"status": "ok"})


def create_upload_routes() -> list[Route]:
    """Create upload routes.

    Returns:
        List of Starlette routes
    """
    return [Route("/nest/upload", handle_upload, methods=["POST"])]
