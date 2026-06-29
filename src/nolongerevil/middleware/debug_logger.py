"""Debug logging middleware for request/response inspection."""

import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from aiohttp import web

from nolongerevil.config import settings
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.serial_parser import extract_serial_from_request

logger = get_logger(__name__)

_MiddlewareType = Callable[
    [web.Request, Callable[[web.Request], Awaitable[web.StreamResponse]]],
    Awaitable[web.StreamResponse],
]


def create_debug_logger_middleware() -> _MiddlewareType:
    """Create the debug logger middleware.

    Returns:
        Middleware function (or passthrough if debug logging disabled)
    """
    if not settings.debug_logging:
        # Return a passthrough middleware
        @web.middleware
        async def passthrough(
            request: web.Request,
            handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
        ) -> web.StreamResponse:
            return await handler(request)

        return passthrough

    # Ensure debug logs directory exists
    log_dir = Path(settings.debug_logs_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    @web.middleware
    async def debug_logger_middleware(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        """Log request and response details to individual JSON files."""
        # Generate request ID
        timestamp = int(time.time() * 1000)
        route = request.path.replace("/", "_").strip("_") or "root"
        request_id = f"{timestamp}_{route}"

        # Extract serial if available
        serial = extract_serial_from_request(request)

        # Capture request details
        try:
            request_body = await request.text()
            try:
                request_json = json.loads(request_body) if request_body else None
            except json.JSONDecodeError:
                request_json = None
        except Exception:
            request_body = None
            request_json = None

        request_data = {
            "method": request.method,
            "path": request.path,
            "query": dict(request.query),
            "headers": dict(request.headers),
            "body": request_json or request_body,
            "serial": serial,
        }

        start_time = time.time()

        try:
            response = await handler(request)
            elapsed = time.time() - start_time

            body_content = None

            if isinstance(response, web.Response) and response.body is not None:
                try:
                    body_content = (
                        response.text
                        if response.text
                        else response.body.decode("utf-8", errors="replace")
                    )
                except Exception:
                    body_content = str(response.body)

            # Capture response details
            response_data = {
                "status": response.status,
                "headers": dict(response.headers),
                "elapsed_ms": round(elapsed * 1000, 2),
                "body": body_content,
            }

            # Log to file
            log_entry = {
                "request_id": request_id,
                "timestamp": timestamp,
                "request": request_data,
                "response": response_data,
            }

            log_file = log_dir / f"{request_id}.json"
            with open(log_file, "w") as f:
                json.dump(log_entry, f, indent=2, default=str)

            logger.debug(
                f"[{request_id}] {request.method} {request.path} -> {response.status} "
                f"({response_data['elapsed_ms']}ms)"
            )

            return response

        except Exception as e:
            elapsed = time.time() - start_time

            # Log error
            log_entry = {
                "request_id": request_id,
                "timestamp": timestamp,
                "request": request_data,
                "error": {
                    "type": type(e).__name__,
                    "message": str(e),
                    "elapsed_ms": round(elapsed * 1000, 2),
                },
            }

            log_file = log_dir / f"{request_id}_error.json"
            with open(log_file, "w") as f:
                json.dump(log_entry, f, indent=2, default=str)

            logger.error(f"[{request_id}] {request.method} {request.path} -> ERROR: {e}")
            raise

    return debug_logger_middleware
