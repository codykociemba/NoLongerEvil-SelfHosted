"""Web UI routes - serves the device management HTML interface."""

from pathlib import Path

from aiohttp import web

from nolongerevil.config import settings
from nolongerevil.lib.logger import get_logger

logger = get_logger(__name__)

# Path to HTML template (CSS and JS are inlined to avoid ingress path issues)
TEMPLATE_DIR = Path(__file__).parent / "templates"
INDEX_TEMPLATE = TEMPLATE_DIR / "index.html"
NLE_ICON = TEMPLATE_DIR / "nle-icon.png"
NLE_FAVICON = TEMPLATE_DIR / "nle-favicon.png"


async def handle_webui(request: web.Request) -> web.Response:
    """Handle GET / - serve the web UI.

    Reads X-Ingress-Path header for Home Assistant ingress support
    and injects it into the HTML via a data attribute.
    """
    ingress_path = request.headers.get("X-Ingress-Path", "")
    html = INDEX_TEMPLATE.read_text()

    # Inject the ingress path via data attribute on body tag
    login = "1" if settings.control_auth and not ingress_path else ""
    html = html.replace("<body>", f'<body data-ingress-path="{ingress_path}" data-login="{login}">')

    return web.Response(text=html, content_type="text/html")


async def handle_icon(_request: web.Request) -> web.Response:
    """Serve the NLE icon."""
    return web.Response(body=NLE_ICON.read_bytes(), content_type="image/png")


async def handle_favicon(_request: web.Request) -> web.Response:
    """Serve the NLE favicon."""
    return web.Response(body=NLE_FAVICON.read_bytes(), content_type="image/png")


def create_webui_routes(app: web.Application) -> None:
    """Register web UI routes."""
    app.router.add_get("/", handle_webui)
    app.router.add_get("/nle-icon.png", handle_icon)
    app.router.add_get("/nle-favicon.png", handle_favicon)
    logger.info("Web UI routes registered")
