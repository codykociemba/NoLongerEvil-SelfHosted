"""Nest pro_info endpoint - installer information lookup."""

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from nolongerevil.lib.logger import get_logger

logger = get_logger(__name__)


async def handle_pro_info(request: Request) -> JSONResponse:
    """Handle installer information lookup request.

    The {code} path parameter is typically a pro installer code.
    Since we're self-hosted, we return a generic response.

    Returns:
        JSON response with installer info (or empty)
    """
    code = request.path_params.get("code", "")

    logger.debug(f"Pro info request for code: {code}")

    # Return empty/default pro info
    return JSONResponse({
        "pro_id": code,
        "company_name": "Self-Hosted",
        "phone": "",
        "email": "",
    })


def create_pro_info_routes() -> list[Route]:
    """Create pro_info routes.

    Returns:
        List of Starlette routes
    """
    return [Route("/nest/pro_info/{code:path}", handle_pro_info, methods=["GET"])]
