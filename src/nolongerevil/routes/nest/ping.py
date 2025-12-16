"""Nest ping endpoint - health check."""

import time

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from nolongerevil.lib.logger import get_logger

logger = get_logger(__name__)


async def handle_ping(request: Request) -> JSONResponse:
    """Handle Nest health check request.

    Returns:
        JSON response with status and timestamp
    """
    return JSONResponse({"status": "ok", "timestamp": int(time.time() * 1000)})


def create_ping_routes() -> list[Route]:
    """Create ping routes.

    Returns:
        List of Starlette routes
    """
    return [Route("/nest/ping", handle_ping, methods=["GET"])]
