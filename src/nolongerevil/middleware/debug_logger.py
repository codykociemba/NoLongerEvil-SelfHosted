"""Debug logging middleware for request/response inspection."""

import json
import time
from pathlib import Path
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from nolongerevil.config import settings
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.serial_parser import extract_serial_from_request

logger = get_logger(__name__)


class DebugLoggerMiddleware(BaseHTTPMiddleware):
    """Middleware that logs request and response details to individual JSON files."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self.log_dir = Path(settings.debug_logs_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Log request and response details to individual JSON files."""
        # Generate request ID
        timestamp = int(time.time() * 1000)
        route = request.url.path.replace("/", "_").strip("_") or "root"
        request_id = f"{timestamp}_{route}"

        # Extract serial if available
        serial = extract_serial_from_request(request)

        # Capture request details
        try:
            request_body = await request.body()
            request_text = request_body.decode("utf-8") if request_body else None
            try:
                request_json = json.loads(request_text) if request_text else None
            except json.JSONDecodeError:
                request_json = None
        except Exception:
            request_text = None
            request_json = None

        request_data = {
            "method": request.method,
            "path": request.url.path,
            "query": dict(request.query_params),
            "headers": dict(request.headers),
            "body": request_json or request_text,
            "serial": serial,
        }

        start_time = time.time()

        try:
            response = await call_next(request)
            elapsed = time.time() - start_time

            # Capture response details
            response_data = {
                "status": response.status_code,
                "headers": dict(response.headers),
                "elapsed_ms": round(elapsed * 1000, 2),
            }

            # Log to file
            log_entry = {
                "request_id": request_id,
                "timestamp": timestamp,
                "request": request_data,
                "response": response_data,
            }

            log_file = self.log_dir / f"{request_id}.json"
            with open(log_file, "w") as f:
                json.dump(log_entry, f, indent=2, default=str)

            logger.debug(
                f"[{request_id}] {request.method} {request.url.path} -> {response.status_code} "
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

            log_file = self.log_dir / f"{request_id}_error.json"
            with open(log_file, "w") as f:
                json.dump(log_entry, f, indent=2, default=str)

            logger.error(f"[{request_id}] {request.method} {request.url.path} -> ERROR: {e}")
            raise


def create_debug_logger_middleware() -> type[BaseHTTPMiddleware] | None:
    """Create the debug logger middleware class.

    Returns:
        Middleware class or None if debug logging is disabled
    """
    if not settings.debug_logging:
        return None
    return DebugLoggerMiddleware
