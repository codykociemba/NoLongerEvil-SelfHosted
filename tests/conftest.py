"""Pytest fixtures and configuration."""

import asyncio
import gc
import tempfile
import threading
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient

from nolongerevil.services.device_availability import DeviceAvailability
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.sqlite3_service import SQLite3Service
from nolongerevil.services.subscription_manager import SubscriptionManager
from nolongerevil.services.weather_service import WeatherService


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_db_path() -> Generator[str, None, None]:
    """Create a temporary database path."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        yield f.name
    Path(f.name).unlink(missing_ok=True)


@pytest_asyncio.fixture
async def sqlite_service(temp_db_path: str) -> AsyncGenerator[SQLite3Service, None]:
    """Create and initialize a SQLite3Service."""
    service = SQLite3Service(temp_db_path)
    await service.initialize()
    yield service
    await service.close()


@pytest_asyncio.fixture
async def state_service(
    sqlite_service: SQLite3Service,
) -> AsyncGenerator[DeviceStateService, None]:
    """Create and initialize a DeviceStateService."""
    service = DeviceStateService(sqlite_service)
    await service.initialize()
    yield service
    await service.close()


@pytest.fixture
def subscription_manager() -> SubscriptionManager:
    """Create a SubscriptionManager."""
    return SubscriptionManager()


@pytest_asyncio.fixture
async def weather_service(
    sqlite_service: SQLite3Service,
) -> AsyncGenerator[WeatherService, None]:
    """Create and initialize a WeatherService."""
    service = WeatherService(sqlite_service)
    await service.initialize()
    yield service
    await service.close()


@pytest.fixture
def device_availability(
    subscription_manager: SubscriptionManager,
) -> DeviceAvailability:
    """Create a DeviceAvailability service."""
    return DeviceAvailability(subscription_manager)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Clean up any lingering threads after test session.

    This is a workaround for pytest-asyncio hanging on Python 3.14 due to
    deprecated asyncio.get_event_loop_policy() calls leaving orphaned threads.
    """
    import os
    import sys

    # Force garbage collection to clean up any lingering async resources
    gc.collect()

    # Give threads a moment to clean up
    for thread in threading.enumerate():
        if thread is not threading.main_thread() and thread.daemon:
            thread.join(timeout=0.1)

    # On Python 3.14+, pytest-asyncio can leave orphaned threads due to
    # deprecated event loop policy APIs. Force exit to avoid hanging.
    if sys.version_info >= (3, 14):
        os._exit(exitstatus)
