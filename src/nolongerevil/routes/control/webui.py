"""Web UI routes - serves the device management HTML interface."""

from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route

from nolongerevil.lib.logger import get_logger

logger = get_logger(__name__)

# Path to HTML template (CSS and JS are inlined to avoid ingress path issues)
TEMPLATE_DIR = Path(__file__).parent / "templates"
INDEX_TEMPLATE = TEMPLATE_DIR / "index.html"


async def handle_webui(request: Request) -> HTMLResponse:
    """Handle GET / - serve the web UI.

    Reads X-Ingress-Path header for Home Assistant ingress support
    and injects it into the HTML via a data attribute.
    """
    ingress_path = request.headers.get("X-Ingress-Path", "")
    html = INDEX_TEMPLATE.read_text()

    # Inject the ingress path via data attribute on body tag
    html = html.replace("<body>", f'<body data-ingress-path="{ingress_path}">')

    return HTMLResponse(html)


def create_webui_routes() -> list[Route]:
    """Create web UI routes.

    Returns:
        List of Starlette routes
    """
    return [Route("/", handle_webui, methods=["GET"])]
