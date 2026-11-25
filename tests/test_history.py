"""Thermostat history: recording modes, change log, retention, summaries, API."""

import csv
import io
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from aiohttp import web

from nolongerevil.lib.change_source import change_source
from nolongerevil.lib.types import DeviceStateChange
from nolongerevil.routes.control.history import create_history_routes
from nolongerevil.services import history as history_module
from nolongerevil.services.history import BIT, HistoryService, Sample, validate_settings

SERIAL = "02AA01AB501203EQ"
T0 = 1_780_000_000.0  # A fixed "now", in seconds


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(history_module.time, "time", c)
    return c


@pytest.fixture
async def history(tmp_path, clock):
    state = {"sample": Sample(0, 20.0, 45.0, 21.0, None, None, 5.0, "heat", BIT["online"])}

    async def read(_serial: str) -> Sample:
        return replace(state["sample"], ts=int(clock.now * 1000))

    service = HistoryService(str(tmp_path / "history.sqlite"), read, lambda: [SERIAL])
    service.state = state  # type: ignore[attr-defined]
    await service.start(background=False)
    yield service
    await service.stop()


async def count(service: HistoryService, table: str = "samples") -> int:
    async with service.db.execute(f"SELECT count(*) FROM {table}") as cur:
        return (await cur.fetchone())[0]


def set_reading(service: HistoryService, **changes) -> None:
    service.state["sample"] = replace(service.state["sample"], **changes)  # type: ignore[attr-defined]


async def test_interval_mode(history, clock):
    await history.update_settings({"mode": "interval", "interval_s": 60})
    await history.record(SERIAL)
    set_reading(history, flags=BIT["heat"])
    await history.record(SERIAL, event=True)  # Changes wait for the interval
    clock.now += 30
    await history.record(SERIAL)
    assert await count(history) == 1
    clock.now += 31
    await history.record(SERIAL)
    assert await count(history) == 2


async def test_changes_mode(history, clock):
    await history.update_settings({"mode": "changes", "temp_step": 0.5, "keepalive_s": 900})
    await history.record(SERIAL)
    clock.now += 1
    set_reading(history, temp=20.2)
    await history.record(SERIAL, event=True)
    assert await count(history) == 1  # Below the step
    clock.now += 1
    set_reading(history, temp=20.6)
    await history.record(SERIAL, event=True)
    clock.now += 1
    set_reading(history, flags=BIT["heat"] | BIT["online"])
    await history.record(SERIAL, event=True)
    assert await count(history) == 3
    clock.now += 900
    await history.record(SERIAL)  # Keep-alive row
    assert await count(history) == 4


async def test_both_mode_records_equipment_changes_at_once(history, clock):
    await history.update_settings({"mode": "both", "interval_s": 300})
    await history.record(SERIAL)
    clock.now += 1
    set_reading(history, temp=25.0)
    await history.record(SERIAL, event=True)
    assert await count(history) == 1  # Readings wait for the interval
    clock.now += 1
    set_reading(history, flags=BIT["cool"])
    await history.record(SERIAL, event=True)
    assert await count(history) == 2


async def test_disabled_thermostat_is_not_recorded(history):
    await history.update_settings({"disabled": [SERIAL]})
    await history.record(SERIAL)
    assert await count(history) == 0


async def test_change_log_records_the_source(history):
    change = DeviceStateChange(
        serial=SERIAL,
        object_key=f"shared.{SERIAL}",
        old_value={"target_temperature": 20.0},
        new_value={"target_temperature": 21.5},
        changed_fields=["target_temperature"],
    )
    await history.on_state_change(change)
    token = change_source.set("dashboard")
    try:
        await history.on_state_change(change)
    finally:
        change_source.reset(token)
    first = DeviceStateChange(SERIAL, f"shared.{SERIAL}", None, {"away": True}, ["away"])
    await history.on_state_change(first)  # First report is not a change
    events = await history.events([SERIAL], 0, 2**62)
    assert [(e["field"], e["old"], e["new"], e["source"]) for e in events] == [
        ("target", "20.0", "21.5", "thermostat"),
        ("target", "20.0", "21.5", "dashboard"),
    ]


async def insert(service: HistoryService, rows: list[tuple[int, float, int]]) -> None:
    await service.db.executemany(
        "INSERT INTO samples VALUES (?, ?, ?, 45, 21, NULL, NULL, ?, 'heat', ?)",
        [(SERIAL, ts, temp, 8.0, flags) for ts, temp, flags in rows],
    )
    await service.db.commit()


async def test_retention_by_days(history, clock):
    now = int(clock.now * 1000)
    await insert(history, [(now - 40 * 86400000, 20, 0), (now - 1000, 20, 0)])
    await history.update_settings({"retention": "days", "retention_days": 30})
    assert await count(history) == 1


async def test_retention_by_size(history, clock):
    now = int(clock.now * 1000)
    await insert(history, [(now - i * 1000, 20.0 + i % 7, i % 3) for i in range(60000)])
    assert await history.size_bytes() > 1_000_000
    await history.update_settings({"retention": "size", "retention_mb": 1})
    assert await history.size_bytes() <= 1_000_000
    async with history.db.execute("SELECT max(ts) FROM samples") as cur:
        assert (await cur.fetchone())[0] == now  # The newest data is kept


async def test_daily_summary(history, clock):
    await history.update_settings({"timezone": "UTC", "mode": "interval", "interval_s": 600})
    midnight = int(datetime(2026, 5, 1, tzinfo=UTC).timestamp() * 1000)
    clock.now = midnight / 1000 + 86400 + 3600  # The next day
    heating = BIT["heat"]
    await insert(
        history,
        [(midnight + i * 600_000, 20.0, heating if i < 12 else 0) for i in range(144)],
    )
    await history.update_daily()
    [day] = await history.daily([SERIAL], "2026-05-01", "2026-05-01")
    assert day["heat_s"] == 2 * 3600
    assert day["temp_avg"] == pytest.approx(20.0)
    assert day["hdd"] == pytest.approx(10.0)  # 18 °C base, 8 °C outside


def test_settings_validation():
    with pytest.raises(ValueError, match="between"):
        validate_settings({"interval_s": 5})
    with pytest.raises(ValueError, match="time zone"):
        validate_settings({"timezone": "Mars/Base"})
    with pytest.raises(ValueError, match="Invalid"):
        validate_settings({"mode": "sometimes"})
    assert validate_settings({"interval_s": 30, "mode": "changes"}) == {
        "interval_s": 30,
        "mode": "changes",
    }


@pytest.fixture
async def client(aiohttp_client, history):
    app = web.Application()
    create_history_routes(app, history, lambda: {SERIAL: "Hallway"})
    return await aiohttp_client(app)


async def test_query_is_downsampled(client, history, clock):
    now = int(clock.now * 1000)
    await insert(history, [(now - i * 60_000, 20.0, BIT["heat"] * (i % 2)) for i in range(1000)])
    r = await client.get(
        f"/api/history?serials={SERIAL}&from={now - 1000 * 60_000}&to={now + 1}&points=100"
    )
    data = (await r.json())[SERIAL]
    assert len(data["ts"]) <= 101
    assert 0.4 < data["heat"][5] < 0.6  # Fraction of the slot with heat on


async def test_csv_export_in_fahrenheit(client, history, clock):
    now = int(clock.now * 1000)
    await insert(history, [(now - 1000, 20.0, BIT["heat"])])
    r = await client.get(f"/api/history/export?serials={SERIAL}&format=csv&unit=F&from=0")
    assert r.headers["Content-Disposition"].startswith("attachment")
    rows = list(csv.DictReader(io.StringIO(await r.text())))
    assert rows[0]["name"] == "Hallway"
    assert float(rows[0]["temp_f"]) == pytest.approx(68.0)
    assert rows[0]["heat"] == "1" and rows[0]["cool"] == "0"


async def test_xlsx_export(client, history, clock):
    await insert(history, [(int(clock.now * 1000) - 1000, 20.0, 0)])
    r = await client.get(f"/api/history/export?serials={SERIAL}&format=xlsx&from=0")
    body = await r.read()
    assert r.status == 200 and body[:2] == b"PK"  # An .xlsx file is a zip archive


async def test_settings_api(client):
    r = await client.put("/api/history/settings", json={"interval_s": 1})
    assert r.status == 400
    r = await client.put("/api/history/settings", json={"interval_s": 120})
    assert (await r.json())["interval_s"] == 120
    status = await (await client.get("/api/history/status")).json()
    assert status["samples"] == 0 and status["size_bytes"] > 0
