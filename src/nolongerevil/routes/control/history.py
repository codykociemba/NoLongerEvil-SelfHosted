"""History API: samples, change log, daily summaries, export and settings."""

import csv
import io
import os
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import xlsxwriter
from aiohttp import web

from nolongerevil.services.history import DAILY_COLUMNS, FLAGS, VALUES, HistoryService

DAY_MS = 86400000
EXCEL_MAX_ROWS = 1_048_575  # Per sheet, below the header


def _time(value: str | None, default: int) -> int:
    """Milliseconds from a ms timestamp or an ISO date/time; ISO without zone means UTC."""
    if not value:
        return default
    if value.isdigit():
        return int(value)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def _range(request: web.Request, default_days: int) -> tuple[list[str], int, int]:
    now = int(time.time() * 1000)
    try:
        end = _time(request.query.get("to"), now)
        start = _time(request.query.get("from"), end - default_days * DAY_MS)
    except ValueError:
        raise web.HTTPBadRequest(text="from/to must be a timestamp in ms or an ISO date")
    serials = [s for s in request.query.get("serials", "").split(",") if s]
    if not serials:
        raise web.HTTPBadRequest(text="serials is required")
    return serials, start, end


def _export_rows(
    kind: str, row: tuple[Any, ...], tz: ZoneInfo, fahrenheit: bool, names: dict[str, str]
) -> list[Any]:
    def temp(value: Any) -> Any:
        return round(value * 9 / 5 + 32, 2) if fahrenheit and value is not None else value

    serial = row[0]
    if kind == "daily":
        values = list(row[2:])
        for i, col in enumerate(DAILY_COLUMNS):
            if col.startswith(("temp_", "outdoor_")):
                values[i] = temp(values[i])
            elif col in ("hdd", "cdd") and fahrenheit and values[i] is not None:
                values[i] = round(values[i] * 9 / 5, 2)
            elif col.endswith("_s") and values[i] is not None:
                values[i] = round(values[i] / 3600, 3)  # Hours
        return [names.get(serial, serial), serial, row[1], *values]
    ts = row[1]
    local = datetime.fromtimestamp(ts / 1000, tz).strftime("%Y-%m-%d %H:%M:%S")
    utc = datetime.fromtimestamp(ts / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if kind == "events":
        return [names.get(serial, serial), serial, local, utc, *row[2:]]
    readings = [
        temp(v) if name != "humidity" else v for name, v in zip(VALUES, row[2:8], strict=True)
    ]
    flags = row[9]
    return [
        names.get(serial, serial), serial, local, utc, *readings, row[8],
        *(int(bool(flags & (1 << i))) for i in range(len(FLAGS))),
    ]  # fmt: skip


def _header(kind: str, unit: str) -> list[str]:
    deg = "f" if unit == "F" else "c"
    if kind == "daily":
        cols = [
            c.replace("_s", "_hours") if c.endswith("_s")
            else f"{c}_{deg}" if c.startswith(("temp_", "outdoor_")) or c in ("hdd", "cdd")
            else c
            for c in DAILY_COLUMNS
        ]  # fmt: skip
        return ["name", "serial", "day", *cols]
    if kind == "events":
        return ["name", "serial", "local_time", "utc_time", "field", "old", "new", "source"]
    values = [v if v == "humidity" else f"{v}_{deg}" for v in VALUES]
    return ["name", "serial", "local_time", "utc_time", *values, "mode", *FLAGS]


def create_history_routes(
    app: web.Application, history: HistoryService, names: Callable[[], dict[str, str]]
) -> None:
    async def get_history(request: web.Request) -> web.Response:
        serials, start, end = _range(request, 1)
        points = min(max(int(request.query.get("points", 2000)), 10), 20000)
        return web.json_response(await history.query(serials, start, end, points))

    async def get_events(request: web.Request) -> web.Response:
        serials, start, end = _range(request, 1)
        return web.json_response(await history.events(serials, start, end))

    async def get_daily(request: web.Request) -> web.Response:
        serials = [s for s in request.query.get("serials", "").split(",") if s]
        first, last = request.query.get("from", "0000-01-01"), request.query.get("to", "9999-12-31")
        if not serials:
            raise web.HTTPBadRequest(text="serials is required")
        return web.json_response(await history.daily(serials, first, last))

    async def export(request: web.Request) -> web.StreamResponse:
        kind = request.query.get("kind", "samples")
        fmt = request.query.get("format", "csv")
        unit = request.query.get("unit", "C").upper()
        if kind not in ("samples", "events", "daily") or fmt not in ("csv", "xlsx"):
            raise web.HTTPBadRequest(text="kind must be samples|events|daily, format csv|xlsx")
        serials, start, end = _range(request, 30)
        tz = ZoneInfo(history.settings["timezone"])
        device_names = names()
        header = _header(kind, unit)
        filename = f"nolongerevil-{kind}-{datetime.now(tz):%Y%m%d-%H%M}.{fmt}"
        disposition = f'attachment; filename="{filename}"'

        if fmt == "csv":
            response = web.StreamResponse(
                headers={"Content-Type": "text/csv", "Content-Disposition": disposition}
            )
            await response.prepare(request)
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(header)
            async for row in history.rows(kind, serials, start, end):
                writer.writerow(_export_rows(kind, row, tz, unit == "F", device_names))
                if buffer.tell() > 65536:
                    await response.write(buffer.getvalue().encode())
                    buffer.seek(0)
                    buffer.truncate()
            await response.write(buffer.getvalue().encode())
            await response.write_eof()
            return response

        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        try:
            book = xlsxwriter.Workbook(path, {"constant_memory": True})
            bold = book.add_format({"bold": True})
            sheet, n = None, EXCEL_MAX_ROWS
            async for row in history.rows(kind, serials, start, end):
                if n >= EXCEL_MAX_ROWS:  # Start a new sheet when one is full
                    sheet = book.add_worksheet()
                    sheet.write_row(0, 0, header, bold)
                    sheet.freeze_panes(1, 0)
                    n = 0
                n += 1
                sheet.write_row(n, 0, _export_rows(kind, row, tz, unit == "F", device_names))  # type: ignore[union-attr]
            if sheet is None:
                book.add_worksheet().write_row(0, 0, header, bold)
            book.close()
            with open(path, "rb") as f:
                body = f.read()
        finally:
            os.unlink(path)
        return web.Response(
            body=body,
            headers={
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "Content-Disposition": disposition,
            },
        )

    async def get_settings(_request: web.Request) -> web.Response:
        return web.json_response(history.settings)

    async def put_settings(request: web.Request) -> web.Response:
        try:
            return web.json_response(await history.update_settings(await request.json()))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

    async def get_status(_request: web.Request) -> web.Response:
        return web.json_response(await history.status())

    app.router.add_get("/api/history", get_history)
    app.router.add_get("/api/history/events", get_events)
    app.router.add_get("/api/history/daily", get_daily)
    app.router.add_get("/api/history/export", export)
    app.router.add_get("/api/history/settings", get_settings)
    app.router.add_put("/api/history/settings", put_settings)
    app.router.add_get("/api/history/status", get_status)
