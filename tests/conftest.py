"""Pytest fixtures and configuration."""

import gc
import tempfile
import threading
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
import pytest_asyncio

from nolongerevil.services.device_availability import DeviceAvailability
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.sqlmodel_service import SQLModelService
from nolongerevil.services.subscription_manager import SubscriptionManager
from nolongerevil.services.weather_service import WeatherService


@pytest.fixture
def temp_db_path() -> Generator[str, None, None]:
    """Create a temporary database path."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        yield f.name
    Path(f.name).unlink(missing_ok=True)


@pytest_asyncio.fixture
async def sqlmodel_service(temp_db_path: str) -> AsyncGenerator[SQLModelService, None]:
    """Create and initialize a SQLModelService."""
    db_url = f"sqlite+aiosqlite:///{temp_db_path}"
    service = SQLModelService(db_url)
    await service.initialize()
    yield service
    await service.close()


@pytest_asyncio.fixture
async def state_service(
    sqlmodel_service: SQLModelService,
) -> AsyncGenerator[DeviceStateService, None]:
    """Create and initialize a DeviceStateService."""
    service = DeviceStateService(sqlmodel_service)
    await service.initialize()
    yield service
    await service.close()


@pytest.fixture
def subscription_manager() -> SubscriptionManager:
    """Create a SubscriptionManager."""
    return SubscriptionManager()


@pytest_asyncio.fixture
async def weather_service(
    sqlmodel_service: SQLModelService,
) -> AsyncGenerator[WeatherService, None]:
    """Create and initialize a WeatherService."""
    service = WeatherService(sqlmodel_service)
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
