"""Web UI routes - serves the device management HTML interface."""

from pathlib import Path

from aiohttp import web

from nolongerevil.lib.logger import get_logger

logger = get_logger(__name__)

# Paths to static files and templates
BASE_DIR = Path(__file__).parent
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
INDEX_TEMPLATE = TEMPLATE_DIR / "index.html"

# Content types for static files
CONTENT_TYPES = {
    ".css": "text/css",
    ".js": "application/javascript",
}


async def handle_webui(request: web.Request) -> web.Response:
    """Handle GET / - serve the web UI.

    Reads X-Ingress-Path header for Home Assistant ingress support
    and injects it into the HTML via a data attribute.
    """
    ingress_path = request.headers.get("X-Ingress-Path", "")
    html = INDEX_TEMPLATE.read_text()

    # Inject the ingress path via data attribute on body tag
    html = html.replace("<body>", f'<body data-ingress-path="{ingress_path}">')

    return web.Response(text=html, content_type="text/html")


async def handle_static(request: web.Request) -> web.Response:
    """Handle GET /static/{filename} - serve static files (CSS, JS)."""
    filename = request.match_info.get("filename", "")

    # Security: only allow specific extensions and no path traversal
    if ".." in filename or "/" in filename:
        raise web.HTTPNotFound()

    file_path = STATIC_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        raise web.HTTPNotFound()

    # Determine content type
    suffix = file_path.suffix.lower()
    content_type = CONTENT_TYPES.get(suffix, "application/octet-stream")

    return web.Response(text=file_path.read_text(), content_type=content_type)


def create_webui_routes(app: web.Application) -> None:
    """Register web UI routes."""
    app.router.add_get("/", handle_webui)
    app.router.add_get("/static/{filename}", handle_static)
    logger.info("Web UI routes registered")
