"""Nest upload endpoint - device log file upload."""

import gzip
from datetime import datetime
from pathlib import Path

from aiohttp import web

from nolongerevil.config.environment import settings
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.serial_parser import extract_serial_from_request

logger = get_logger(__name__)

LOG_STORAGE_PATH = Path("/app/data/device_logs")


async def handle_upload(request: web.Request) -> web.Response:
    """Handle device log file upload.

    Stores logs if STORE_DEVICE_LOGS env var is enabled.
    Logs are organized by device serial in subdirectories.
    """
    serial = extract_serial_from_request(request)

    try:
        data = await request.read()
        size = len(data)
        logger.info(f"Received log upload from device {serial or 'unknown'}: {size} bytes")

        if settings.store_device_logs:
            device_dir = LOG_STORAGE_PATH / (serial or "unknown")
            device_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = device_dir / f"{timestamp}.log"

            # Decompress if gzipped
            try:
                decompressed = gzip.decompress(data)
                log_file.write_bytes(decompressed)
                logger.debug(f"Stored decompressed log ({len(decompressed)} bytes) to {log_file}")
            except gzip.BadGzipFile:
                log_file.write_bytes(data)
                logger.debug(f"Stored raw log to {log_file}")

    except Exception as e:
        logger.warning(f"Failed to read/store upload data: {e}")

    return web.json_response({"status": "ok"})


def create_upload_routes(app: web.Application) -> None:
    """Register upload routes."""
    app.router.add_post("/nest/upload", handle_upload)
