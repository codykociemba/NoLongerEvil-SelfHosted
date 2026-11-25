"""Login, first-time setup, device links and account routes for the control panel."""

import asyncio
import io
import secrets
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import segno
from aiohttp import web

from nolongerevil.config import settings
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import APIKey, APIKeyPermissions
from nolongerevil.middleware.api_key_auth import hash_api_key
from nolongerevil.middleware.control_auth import COOKIE
from nolongerevil.services.control_auth import MIN_PASSWORD_LENGTH, ControlAuth
from nolongerevil.services.sqlmodel_service import SQLModelService

logger = get_logger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
API_KEY_USER = "homeassistant"


def panel_url(path: str) -> str:
    """Address of the control panel as reached from the LAN."""
    host = urlparse(settings.api_origin).hostname or "localhost"
    return f"http://{host}:{settings.control_port}{path}"


def log_setup_code(auth: ControlAuth) -> None:
    """Print the one-time setup link and its QR code to the server log."""
    url = panel_url(f"/setup?code={auth.new_code('setup')}")
    qr = io.StringIO()
    segno.make(url, error="m").terminal(out=qr, compact=True, border=2)
    logger.warning(
        "Control panel has no password yet. Open this link (valid 1 hour) to set one, "
        f"or scan the QR code with your phone:\n{url}\n{qr.getvalue()}"
    )


def _device_name(request: web.Request) -> str:
    ua = request.headers.get("User-Agent", "")
    browser = next(
        (b for b in ("Edg", "Firefox", "Chrome", "Safari") if b in ua), "Browser"
    ).replace("Edg", "Edge")
    system = next(
        (s for s in ("Android", "iPhone", "iPad", "Windows", "Mac OS", "Linux") if s in ua), ""
    ).replace("Mac OS", "macOS")
    return f"{browser} on {system}" if system else browser


def _client(request: web.Request) -> str:
    return request.remote or "unknown"


async def _sign_in(
    request: web.Request, auth: ControlAuth, remember: bool, response: web.StreamResponse
) -> web.StreamResponse:
    token = await auth.create_session(_device_name(request), remember)
    response.set_cookie(
        COOKIE,
        token,
        max_age=365 * 24 * 3600 if remember else None,
        httponly=True,
        samesite="Strict",
        secure=request.secure,
        path="/",
    )
    return response


def _page(name: str) -> web.Response:
    return web.Response(text=(TEMPLATE_DIR / name).read_text(), content_type="text/html")


def create_auth_routes(app: web.Application, auth: ControlAuth, storage: SQLModelService) -> None:
    async def login_page(_request: web.Request) -> web.Response:
        return _page("login.html")

    async def login(request: web.Request) -> web.StreamResponse:
        client = _client(request)
        if wait := auth.locked_for(client):
            return web.json_response(
                {"error": f"Too many attempts. Try again in {wait // 60 + 1} min."}, status=429
            )
        body = await request.json()
        if not await auth.check_password(str(body.get("password", ""))):
            await asyncio.sleep(auth.record_failure(client))
            return web.json_response({"error": "Wrong password"}, status=401)
        auth.record_success(client)
        return await _sign_in(request, auth, bool(body.get("remember")), web.json_response({}))

    async def setup(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        password = str(body.get("password", ""))
        if await auth.has_password():
            return web.json_response({"error": "A password is already set"}, status=409)
        if len(password) < MIN_PASSWORD_LENGTH:
            return web.json_response(
                {"error": f"Password must be at least {MIN_PASSWORD_LENGTH} characters"},
                status=400,
            )
        if not auth.use_code(str(body.get("code", "")), "setup"):
            return web.json_response(
                {"error": "Setup link expired or already used. Restart the server for a new one."},
                status=403,
            )
        await auth.set_password(password)
        logger.info("Control panel password set")
        return await _sign_in(request, auth, bool(body.get("remember")), web.json_response({}))

    async def link(request: web.Request) -> web.StreamResponse:
        if not auth.use_code(request.query.get("code", ""), "link"):
            raise web.HTTPFound("/login")
        return await _sign_in(request, auth, True, web.HTTPFound("/"))

    async def logout(request: web.Request) -> web.Response:
        session = request.get("control_session")
        if session:
            await auth.delete_sessions(ids=[session.id])
        response = web.json_response({})
        response.del_cookie(COOKIE, path="/")
        return response

    async def list_sessions(request: web.Request) -> web.Response:
        current = request.get("control_session")
        return web.json_response(
            [
                {
                    "id": s.id,
                    "name": s.name,
                    "remember": s.remember,
                    "created_at": s.createdAt,
                    "last_seen_at": s.lastSeenAt,
                    "current": current is not None and s.id == current.id,
                }
                for s in await auth.list_sessions()
            ]
        )

    async def delete_session(request: web.Request) -> web.Response:
        await auth.delete_sessions(ids=[int(request.match_info["id"])])
        return web.json_response({})

    async def sign_out_others(request: web.Request) -> web.Response:
        current = request.get("control_session")
        await auth.delete_sessions(keep=current.id if current else None)
        return web.json_response({})

    async def change_password(request: web.Request) -> web.Response:
        body = await request.json()
        if not await auth.check_password(str(body.get("current", ""))):
            return web.json_response({"error": "Current password is wrong"}, status=401)
        try:
            await auth.set_password(str(body.get("new", "")))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({})

    async def device_link(_request: web.Request) -> web.Response:
        url = panel_url(f"/link?code={auth.new_code('link')}")
        return web.json_response(
            {"url": url, "svg": segno.make(url, error="m").svg_inline(scale=5)}
        )

    async def list_api_keys(_request: web.Request) -> web.Response:
        keys = await storage.get_user_api_keys(API_KEY_USER)
        return web.json_response(
            [
                {
                    "id": k.id,
                    "name": k.name,
                    "preview": k.key_preview,
                    "created_at": k.created_at.isoformat(),
                    "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                }
                for k in keys
            ]
        )

    async def create_api_key(request: web.Request) -> web.Response:
        body = await request.json()
        raw = "nlapi_" + secrets.token_urlsafe(24)
        await storage.create_api_key(
            APIKey(
                id="",
                key_hash=hash_api_key(raw),
                key_preview=raw[:12] + "...",
                user_id=API_KEY_USER,
                name=str(body.get("name") or "API key")[:60],
                permissions=APIKeyPermissions(),
                created_at=datetime.now(),
            )
        )
        return web.json_response({"key": raw})

    async def delete_api_key(request: web.Request) -> web.Response:
        await storage.delete_api_key(request.match_info["id"])
        return web.json_response({})

    async def account_page(_request: web.Request) -> web.Response:
        return _page("account.html")

    app.router.add_get("/login", login_page)
    app.router.add_post("/login", login)
    app.router.add_get("/setup", login_page)
    app.router.add_post("/setup", setup)
    app.router.add_get("/link", link)
    app.router.add_post("/logout", logout)
    app.router.add_get("/account", account_page)
    app.router.add_get("/api/auth/sessions", list_sessions)
    app.router.add_delete("/api/auth/sessions/{id}", delete_session)
    app.router.add_post("/api/auth/sessions/sign-out-others", sign_out_others)
    app.router.add_post("/api/auth/password", change_password)
    app.router.add_post("/api/auth/device-link", device_link)
    app.router.add_get("/api/auth/api-keys", list_api_keys)
    app.router.add_post("/api/auth/api-keys", create_api_key)
    app.router.add_delete("/api/auth/api-keys/{id}", delete_api_key)
