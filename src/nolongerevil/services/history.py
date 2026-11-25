"""Thermostat history: recording, retention, daily summaries and queries.

Kept in its own SQLite file next to the main database, so its size can be
measured and capped without touching device state.
"""

import asyncio
import json
import math
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite

from nolongerevil.lib.change_source import change_source
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import DeviceStateChange

logger = get_logger(__name__)

# On/off values, stored as bits of one integer
FLAGS = (
    "heat", "heat2", "heat3", "aux_heat", "alt_heat", "emer_heat",
    "cool", "cool2", "cool3", "fan", "humidifier", "dehumidifier", "auto_dehum",
    "away", "eco", "online",
)  # fmt: skip
BIT = {name: 1 << i for i, name in enumerate(FLAGS)}
HEATING = sum(BIT[f] for f in ("heat", "heat2", "heat3", "aux_heat", "alt_heat", "emer_heat"))
COOLING = BIT["cool"] | BIT["cool2"] | BIT["cool3"]
DEHUMIDIFYING = BIT["dehumidifier"] | BIT["auto_dehum"]
VALUES = ("temp", "humidity", "target", "target_low", "target_high", "outdoor")
DEGREE_DAY_BASE_C = 18.0

# Setpoint and mode fields logged as changes, with the bucket they live in
LOGGED_FIELDS = {
    ("shared", "target_temperature"): "target",
    ("shared", "target_temperature_low"): "target_low",
    ("shared", "target_temperature_high"): "target_high",
    ("shared", "target_temperature_type"): "mode",
    ("shared", "away"): "away",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "mode": "both",  # interval | changes | both
    "interval_s": 60,
    "keepalive_s": 900,  # changes mode: at least one row this often
    "temp_step": 0.1,  # changes mode: smallest temperature change (°C) that counts
    "humidity_step": 1.0,
    "retention": "days",  # days | size
    "retention_days": 30,
    "retention_mb": 200,
    "disabled": [],  # serials not recorded
    "timezone": os.environ.get("TZ") or "UTC",  # day boundaries for summaries
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    serial TEXT NOT NULL, ts INTEGER NOT NULL,
    temp REAL, humidity REAL, target REAL, target_low REAL, target_high REAL,
    outdoor REAL, mode TEXT, flags INTEGER NOT NULL,
    PRIMARY KEY (serial, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS events (
    serial TEXT NOT NULL, ts INTEGER NOT NULL,
    field TEXT NOT NULL, old TEXT, new TEXT, source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (serial, ts);
CREATE TABLE IF NOT EXISTS daily (
    serial TEXT NOT NULL, day TEXT NOT NULL,
    heat_s INTEGER, cool_s INTEGER, fan_s INTEGER, humidifier_s INTEGER, dehumidifier_s INTEGER,
    temp_avg REAL, temp_min REAL, temp_max REAL,
    humidity_avg REAL, humidity_min REAL, humidity_max REAL,
    outdoor_avg REAL, hdd REAL, cdd REAL,
    PRIMARY KEY (serial, day)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

DAILY_COLUMNS = (
    "heat_s", "cool_s", "fan_s", "humidifier_s", "dehumidifier_s",
    "temp_avg", "temp_min", "temp_max", "humidity_avg", "humidity_min", "humidity_max",
    "outdoor_avg", "hdd", "cdd",
)  # fmt: skip


@dataclass(frozen=True)
class Sample:
    ts: int
    temp: float | None
    humidity: float | None
    target: float | None
    target_low: float | None
    target_high: float | None
    outdoor: float | None
    mode: str | None
    flags: int

    def row(self, serial: str) -> tuple[Any, ...]:
        return (serial, self.ts, *(getattr(self, v) for v in VALUES), self.mode, self.flags)


CHOICES = {"mode": ("interval", "changes", "both"), "retention": ("days", "size")}


def validate_settings(changes: dict[str, Any]) -> dict[str, Any]:
    """Check user-supplied settings; raise ValueError with a readable message."""
    limits = {
        "interval_s": (10, 86400),
        "keepalive_s": (60, 86400),
        "temp_step": (0, 5),
        "humidity_step": (0, 20),
        "retention_days": (1, 3650),
        "retention_mb": (1, 100000),
    }
    clean: dict[str, Any] = {}
    for key, value in changes.items():
        if key in limits:
            low, high = limits[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{key} must be a number")
            if not low <= value <= high:
                raise ValueError(f"{key} must be between {low} and {high}")
            clean[key] = int(value) if key.endswith(("_s", "_days", "_mb")) else float(value)
        elif value in CHOICES.get(key, ()):
            clean[key] = value
        elif key == "disabled" and isinstance(value, list):
            clean[key] = [str(s) for s in value]
        elif key == "timezone" and isinstance(value, str):
            try:
                ZoneInfo(value)
            except Exception:
                raise ValueError(f"Unknown time zone: {value}")
            clean[key] = value
        else:
            raise ValueError(f"Invalid setting: {key}")
    return clean


def sample_from_status(status: dict[str, Any], outdoor: float | None, ts: int) -> Sample:
    """Build a sample from the dashboard's device status."""
    hvac = status.get("hvac", {})
    on = {
        "heat": hvac.get("heater"),
        "heat2": hvac.get("heat_x2"),
        "heat3": hvac.get("heat_x3"),
        "aux_heat": hvac.get("aux_heat"),
        "alt_heat": hvac.get("alt_heat"),
        "emer_heat": hvac.get("emer_heat"),
        "cool": hvac.get("ac"),
        "cool2": hvac.get("cool_x2"),
        "cool3": hvac.get("cool_x3"),
        "fan": hvac.get("fan"),
        "humidifier": hvac.get("humidifier"),
        "dehumidifier": hvac.get("dehumidifier"),
        "auto_dehum": hvac.get("auto_dehum"),
        "away": status.get("away"),
        "eco": status.get("eco_mode") not in (None, "schedule"),
        "online": status.get("is_available"),
    }

    def number(key: str) -> float | None:
        value = status.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    return Sample(
        ts=ts,
        temp=number("current_temperature"),
        humidity=number("humidity"),
        target=number("target_temperature"),
        target_low=number("target_temperature_low"),
        target_high=number("target_temperature_high"),
        outdoor=outdoor,
        mode=status.get("mode"),
        flags=sum(BIT[name] for name, value in on.items() if value),
    )


Reader = Callable[[str], Awaitable[Sample | None]]


class HistoryService:
    def __init__(self, db_path: str, read: Reader, serials: Callable[[], list[str]]) -> None:
        self._path = db_path
        self._read = read
        self._serials = serials
        self._db: aiosqlite.Connection | None = None
        self._last: dict[str, Sample] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self.settings: dict[str, Any] = dict(DEFAULT_SETTINGS)

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("History not started")
        return self._db

    async def start(self, background: bool = True) -> None:
        self._db = await aiosqlite.connect(self._path)
        await self.db.execute("PRAGMA auto_vacuum=INCREMENTAL")  # Only applies to a new file
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.executescript(SCHEMA)
        async with self.db.execute("SELECT key, value FROM settings") as cur:
            async for key, value in cur:
                self.settings[key] = json.loads(value)
        async with self.db.execute(
            f"SELECT s.serial, s.ts, {', '.join(VALUES)}, mode, flags FROM samples s "
            "JOIN (SELECT serial, max(ts) AS ts FROM samples GROUP BY serial) m "
            "USING (serial, ts)"
        ) as cur:
            async for serial, *values in cur:
                self._last[serial] = Sample(*values)
        await self.db.commit()
        if background:
            self._tasks = [
                asyncio.create_task(self._tick_loop()),
                asyncio.create_task(self._maintenance_loop()),
            ]
        logger.info(f"History recording to {self._path} ({self.settings['mode']} mode)")

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._db:
            await self._db.close()
            self._db = None

    async def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        clean = validate_settings(changes)
        self.settings.update(clean)
        await self.db.executemany(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            [(key, json.dumps(value)) for key, value in clean.items()],
        )
        await self.db.commit()
        await self.run_maintenance()
        return self.settings

    # Recording

    def _period_ms(self) -> int:
        s = self.settings
        return (s["keepalive_s"] if s["mode"] == "changes" else s["interval_s"]) * 1000

    async def on_state_change(self, change: DeviceStateChange) -> None:
        if self._db is None or not change.object_key.startswith(("device.", "shared.")):
            return
        await self._log_changes(change)
        await self.record(change.serial, event=True)

    async def _log_changes(self, change: DeviceStateChange) -> None:
        if change.old_value is None:
            return
        bucket = change.object_key.split(".", 1)[0]
        ts = int(time.time() * 1000)
        rows = []
        for field in change.changed_fields:
            name = LOGGED_FIELDS.get((bucket, field))
            old, new = change.old_value.get(field), change.new_value.get(field)
            if bucket == "device" and field == "eco":
                name = "eco"
                old = old.get("mode") if isinstance(old, dict) else old
                new = new.get("mode") if isinstance(new, dict) else new
            if name and old != new:
                rows.append((change.serial, ts, name, _text(old), _text(new), change_source.get()))
        if rows:
            await self.db.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", rows)
            await self.db.commit()

    async def record(self, serial: str, event: bool = False) -> None:
        """Save a sample for `serial` if the recording mode calls for one now."""
        s = self.settings
        if serial in s["disabled"]:
            return
        last = self._last.get(serial)
        now = int(time.time() * 1000)
        due = last is None or now - last.ts >= self._period_ms()
        if not due and not (event and s["mode"] != "interval"):
            return
        sample = await self._read(serial)
        if sample is None:
            return
        if not due and last is not None and not self._changed(last, sample):
            return
        await self.db.execute(
            "INSERT OR REPLACE INTO samples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            sample.row(serial),
        )
        await self.db.commit()
        self._last[serial] = sample

    def _changed(self, last: Sample, new: Sample) -> bool:
        if (last.flags, last.mode, last.target, last.target_low, last.target_high) != (
            new.flags,
            new.mode,
            new.target,
            new.target_low,
            new.target_high,
        ):
            return True
        if self.settings["mode"] != "changes":
            return False  # In "both" mode readings are covered by the interval
        steps = {
            "temp": self.settings["temp_step"],
            "outdoor": self.settings["temp_step"],
            "humidity": self.settings["humidity_step"],
        }
        for key, step in steps.items():
            a, b = getattr(last, key), getattr(new, key)
            if (a is None) != (b is None) or (
                a is not None and b is not None and abs(a - b) >= max(step, 1e-9)
            ):
                return True
        return False

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            for serial in self._serials():
                try:
                    await self.record(serial)
                except Exception as e:
                    logger.error(f"History sample for {serial} failed: {e}")

    # Retention and daily summaries

    async def _maintenance_loop(self) -> None:
        await asyncio.sleep(5)
        while True:
            try:
                await self.run_maintenance()
            except Exception as e:
                logger.error(f"History maintenance failed: {e}")
            await asyncio.sleep(3600)

    async def run_maintenance(self) -> None:
        await self.update_daily()
        await self.apply_retention()

    async def size_bytes(self) -> int:
        async with self.db.execute("PRAGMA page_count") as cur:
            pages = (await cur.fetchone() or (0,))[0]
        async with self.db.execute("PRAGMA freelist_count") as cur:
            free = (await cur.fetchone() or (0,))[0]
        async with self.db.execute("PRAGMA page_size") as cur:
            page_size = (await cur.fetchone() or (4096,))[0]
        return int((pages - free) * page_size)

    async def apply_retention(self) -> None:
        s = self.settings
        if s["retention"] == "days":
            cutoff = int(time.time() * 1000) - s["retention_days"] * 86400000
            await self._delete_before(cutoff)
        else:
            limit = s["retention_mb"] * 1_000_000
            while await self.size_bytes() > limit:
                async with self.db.execute("SELECT count(*) FROM samples") as cur:
                    count = (await cur.fetchone() or (0,))[0]
                if not count:
                    break
                async with self.db.execute(
                    "SELECT ts FROM samples ORDER BY ts LIMIT 1 OFFSET ?",
                    (max(1, count // 20),),  # Drop the oldest 5% at a time
                ) as cur:
                    row = await cur.fetchone()
                await self._delete_before(row[0] if row else math.inf)
        await self.db.execute("PRAGMA incremental_vacuum")
        await self.db.commit()

    async def _delete_before(self, cutoff: float) -> None:
        await self.db.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        await self.db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        await self.db.commit()

    async def update_daily(self) -> None:
        """(Re)compute daily summaries from the last summarised day up to today."""
        tz = ZoneInfo(self.settings["timezone"])
        today = datetime.now(tz).date()
        async with self.db.execute(
            "SELECT s.serial, min(s.ts), (SELECT max(day) FROM daily d WHERE d.serial = s.serial) "
            "FROM samples s GROUP BY s.serial"
        ) as cur:
            starts = await cur.fetchall()
        for serial, first_ts, last_day in starts:
            day = (
                date.fromisoformat(last_day)
                if last_day
                else datetime.fromtimestamp(first_ts / 1000, tz).date()
            )
            while day <= today:
                await self._summarise_day(serial, day, tz)
                day += timedelta(days=1)
        await self.db.commit()

    async def _summarise_day(self, serial: str, day: date, tz: ZoneInfo) -> None:
        start = int(datetime.combine(day, datetime.min.time(), tz).timestamp() * 1000)
        end = int(
            datetime.combine(day + timedelta(days=1), datetime.min.time(), tz).timestamp() * 1000
        )
        end = min(end, int(time.time() * 1000))
        gap = max(2 * self._period_ms(), 300_000)  # A longer gap means the server was down
        async with self.db.execute(
            f"SELECT ts, {', '.join(VALUES)}, mode, flags FROM samples "
            "WHERE serial = ? AND ts >= ? AND ts < ? ORDER BY ts",
            (serial, start - gap, end),
        ) as cur:
            rows = [Sample(*r) for r in await cur.fetchall()]
        totals = dict.fromkeys(("heat", "cool", "fan", "humidifier", "dehumidifier"), 0.0)
        weighted = {k: [0.0, 0.0] for k in ("temp", "humidity", "outdoor")}  # sum, seconds
        extremes: dict[str, list[float]] = {"temp": [], "humidity": []}
        for i, row in enumerate(rows):
            nxt = rows[i + 1].ts if i + 1 < len(rows) else end
            seconds = max(0, min(nxt, end, row.ts + gap) - max(row.ts, start)) / 1000
            if row.ts >= start:
                for key in extremes:
                    if getattr(row, key) is not None:
                        extremes[key].append(getattr(row, key))
            if not seconds:
                continue
            totals["heat"] += seconds * bool(row.flags & HEATING)
            totals["cool"] += seconds * bool(row.flags & COOLING)
            totals["fan"] += seconds * bool(row.flags & BIT["fan"])
            totals["humidifier"] += seconds * bool(row.flags & BIT["humidifier"])
            totals["dehumidifier"] += seconds * bool(row.flags & DEHUMIDIFYING)
            for key, acc in weighted.items():
                if getattr(row, key) is not None:
                    acc[0] += getattr(row, key) * seconds
                    acc[1] += seconds
        if not any(acc[1] for acc in weighted.values()) and not any(totals.values()):
            return
        avg = {k: (round(acc[0] / acc[1], 2) if acc[1] else None) for k, acc in weighted.items()}
        outdoor = avg["outdoor"]
        values = (
            *(round(totals[k]) for k in ("heat", "cool", "fan", "humidifier", "dehumidifier")),
            avg["temp"], min(extremes["temp"], default=None), max(extremes["temp"], default=None),
            avg["humidity"], min(extremes["humidity"], default=None), max(extremes["humidity"], default=None),
            outdoor,
            None if outdoor is None else max(0.0, DEGREE_DAY_BASE_C - outdoor),
            None if outdoor is None else max(0.0, outdoor - DEGREE_DAY_BASE_C),
        )  # fmt: skip
        await self.db.execute(
            f"INSERT OR REPLACE INTO daily VALUES (?, ?, {', '.join('?' * len(DAILY_COLUMNS))})",
            (serial, day.isoformat(), *values),
        )

    # Queries

    async def query(self, serials: list[str], start: int, end: int, points: int) -> dict[str, Any]:
        """Samples per thermostat as columns, averaged into at most `points` time slots.

        On/off values come back as the fraction of each slot they were on.
        """
        result: dict[str, Any] = {}
        flag_cols = ", ".join(f"avg((flags >> {i}) & 1)" for i in range(len(FLAGS)))
        for serial in serials:
            async with self.db.execute(
                "SELECT count(*) FROM samples WHERE serial = ? AND ts >= ? AND ts < ?",
                (serial, start, end),
            ) as cur:
                count = (await cur.fetchone() or (0,))[0]
            bucket = max(1, math.ceil((end - start) / points)) if count > points else 1
            async with self.db.execute(
                f"SELECT (ts / {bucket}) * {bucket} AS t, "
                f"{', '.join(f'avg({v})' for v in VALUES)}, max(mode), {flag_cols} "
                "FROM samples WHERE serial = ? AND ts >= ? AND ts < ? GROUP BY t ORDER BY t",
                (serial, start, end),
            ) as cur:
                rows = await cur.fetchall()
            columns = ("ts", *VALUES, "mode", *FLAGS)
            result[serial] = {c: [r[i] for r in rows] for i, c in enumerate(columns)}
            result[serial]["bucket_ms"] = bucket if bucket > 1 else None
        return result

    async def events(self, serials: list[str], start: int, end: int) -> list[dict[str, Any]]:
        marks = ", ".join("?" * len(serials))
        async with self.db.execute(
            f"SELECT serial, ts, field, old, new, source FROM events "
            f"WHERE serial IN ({marks}) AND ts >= ? AND ts < ? ORDER BY ts",
            (*serials, start, end),
        ) as cur:
            keys = ("serial", "ts", "field", "old", "new", "source")
            return [dict(zip(keys, r, strict=True)) for r in await cur.fetchall()]

    async def daily(self, serials: list[str], first: str, last: str) -> list[dict[str, Any]]:
        marks = ", ".join("?" * len(serials))
        async with self.db.execute(
            f"SELECT serial, day, {', '.join(DAILY_COLUMNS)} FROM daily "
            f"WHERE serial IN ({marks}) AND day >= ? AND day <= ? ORDER BY day",
            (*serials, first, last),
        ) as cur:
            keys = ("serial", "day", *DAILY_COLUMNS)
            return [dict(zip(keys, r, strict=True)) for r in await cur.fetchall()]

    async def status(self) -> dict[str, Any]:
        async with self.db.execute("SELECT count(*), min(ts), max(ts) FROM samples") as cur:
            samples, oldest, newest = await cur.fetchone() or (0, None, None)
        async with self.db.execute("SELECT count(*) FROM events") as cur:
            events = (await cur.fetchone() or (0,))[0]
        async with self.db.execute("SELECT count(*) FROM daily") as cur:
            days = (await cur.fetchone() or (0,))[0]
        size = await self.size_bytes()
        span_days = (newest - oldest) / 86400000 if oldest is not None and newest is not None else 0
        per_day = size / span_days if span_days >= 1 else None
        limit = self.settings["retention_mb"] * 1_000_000
        size_mode = self.settings["retention"] == "size"
        return {
            "size_bytes": size,
            "samples": samples,
            "events": events,
            "daily_rows": days,
            "oldest": oldest,
            "newest": newest,
            "bytes_per_day": per_day,
            "limit_bytes": limit if size_mode else None,
            "days_until_full": (limit - size) / per_day if size_mode and per_day else None,
        }

    async def rows(
        self, kind: str, serials: list[str], start: int, end: int
    ) -> AsyncIterator[tuple[Any, ...]]:
        """Raw rows for export, oldest first."""
        marks = ", ".join("?" * len(serials))
        if kind == "daily":
            tz = ZoneInfo(self.settings["timezone"])
            first = datetime.fromtimestamp(start / 1000, tz).date().isoformat()
            last = datetime.fromtimestamp(end / 1000, tz).date().isoformat()
            sql = (
                f"SELECT serial, day, {', '.join(DAILY_COLUMNS)} FROM daily "
                f"WHERE serial IN ({marks}) AND day >= ? AND day <= ? ORDER BY serial, day"
            )
            params: tuple[Any, ...] = (*serials, first, last)
        else:
            table, cols = (
                ("events", "serial, ts, field, old, new, source")
                if kind == "events"
                else ("samples", f"serial, ts, {', '.join(VALUES)}, mode, flags")
            )
            sql = (
                f"SELECT {cols} FROM {table} WHERE serial IN ({marks}) "
                "AND ts >= ? AND ts < ? ORDER BY serial, ts"
            )
            params = (*serials, start, end)
        async with self.db.execute(sql, params) as cur:
            async for row in cur:
                yield tuple(row)


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def status_reader(state_service: Any, device_availability: Any, storage: Any) -> Reader:
    """Build the sample reader from the same status the dashboard shows."""
    from nolongerevil.routes.control.status import format_device_status

    async def read(serial: str) -> Sample | None:
        if not state_service.get_object(serial, f"shared.{serial}"):
            return None
        status = format_device_status(serial, state_service, device_availability)
        device = state_service.get_object(serial, f"device.{serial}")
        shared = state_service.get_object(serial, f"shared.{serial}")
        values = [
            (device.value if device else {}).get("outdoor_temperature"),
            (shared.value if shared else {}).get("outside_temperature"),
            (device.value if device else {}).get("outside_temperature"),
        ]
        outdoor = next((v for v in values if isinstance(v, (int, float))), None)
        if outdoor is None:
            owner = await storage.get_device_owner(serial)
            weather = await storage.get_user_weather(owner.user_id) if owner else None
            current = (weather or {}).get("current") or {}
            outdoor = current.get("temp_c") if isinstance(current, dict) else None
        return sample_from_status(status, outdoor, int(time.time() * 1000))

    return read
