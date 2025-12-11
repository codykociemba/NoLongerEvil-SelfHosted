"""Tests for device availability service."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from nolongerevil.services.device_availability import (
    DEFAULT_CHECK_INTERVAL,
    DEFAULT_DEVICE_TIMEOUT,
    DeviceAvailability,
    DeviceStatus,
)


@pytest.fixture
def mock_subscription_manager():
    """Create a mock subscription manager."""
    manager = MagicMock()
    manager.has_active_subscription = MagicMock(return_value=False)
    return manager


@pytest.fixture
def availability_service(mock_subscription_manager):
    """Create a device availability service for testing."""
    return DeviceAvailability(
        subscription_manager=mock_subscription_manager,
        timeout_seconds=60,  # Short timeout for testing
        check_interval_seconds=1,
    )


class TestDeviceStatus:
    """Tests for DeviceStatus dataclass."""

    def test_default_values(self):
        """Test default values for DeviceStatus."""
        status = DeviceStatus(serial="TEST123")
        assert status.serial == "TEST123"
        assert status.is_available is True
        assert isinstance(status.last_seen, datetime)

    def test_custom_values(self):
        """Test custom values for DeviceStatus."""
        custom_time = datetime(2024, 1, 1, 12, 0, 0)
        status = DeviceStatus(
            serial="TEST123",
            last_seen=custom_time,
            is_available=False,
        )
        assert status.last_seen == custom_time
        assert status.is_available is False


class TestDeviceAvailabilityInit:
    """Tests for DeviceAvailability initialization."""

    def test_default_timeout(self, mock_subscription_manager):
        """Test default timeout value."""
        service = DeviceAvailability(mock_subscription_manager)
        assert service._timeout == timedelta(seconds=DEFAULT_DEVICE_TIMEOUT)

    def test_default_check_interval(self, mock_subscription_manager):
        """Test default check interval."""
        service = DeviceAvailability(mock_subscription_manager)
        assert service._check_interval == DEFAULT_CHECK_INTERVAL

    def test_custom_timeout(self, mock_subscription_manager):
        """Test custom timeout value."""
        service = DeviceAvailability(mock_subscription_manager, timeout_seconds=120)
        assert service._timeout == timedelta(seconds=120)


class TestMarkDeviceSeen:
    """Tests for mark_device_seen method."""

    @pytest.mark.asyncio
    async def test_new_device_is_tracked(self, availability_service):
        """Test that new devices are added to tracking."""
        await availability_service.mark_device_seen("NEW_DEVICE")
        assert "NEW_DEVICE" in availability_service._devices
        assert availability_service._devices["NEW_DEVICE"].is_available is True

    @pytest.mark.asyncio
    async def test_existing_device_updates_last_seen(self, availability_service):
        """Test that existing devices update last_seen."""
        await availability_service.mark_device_seen("TEST123")
        first_seen = availability_service._devices["TEST123"].last_seen

        # Small delay to ensure time difference
        await availability_service.mark_device_seen("TEST123")
        second_seen = availability_service._devices["TEST123"].last_seen

        assert second_seen >= first_seen

    @pytest.mark.asyncio
    async def test_unavailable_device_becomes_available(self, availability_service):
        """Test that unavailable device becomes available when seen."""
        # Set up an unavailable device
        availability_service._devices["TEST123"] = DeviceStatus(
            serial="TEST123",
            is_available=False,
            last_seen=datetime.now() - timedelta(hours=1),
        )

        await availability_service.mark_device_seen("TEST123")
        assert availability_service._devices["TEST123"].is_available is True

    @pytest.mark.asyncio
    async def test_notifies_integration_manager_on_new_device(self, availability_service):
        """Test that integration manager is notified of new devices."""
        mock_integration_manager = AsyncMock()
        availability_service.set_integration_manager(mock_integration_manager)

        await availability_service.mark_device_seen("NEW_DEVICE")
        mock_integration_manager.on_device_connected.assert_called_once_with("NEW_DEVICE")

    @pytest.mark.asyncio
    async def test_notifies_integration_manager_on_reconnect(self, availability_service):
        """Test that integration manager is notified when device reconnects."""
        mock_integration_manager = AsyncMock()
        availability_service.set_integration_manager(mock_integration_manager)

        # Set up an unavailable device
        availability_service._devices["TEST123"] = DeviceStatus(
            serial="TEST123",
            is_available=False,
        )

        await availability_service.mark_device_seen("TEST123")
        mock_integration_manager.on_device_connected.assert_called_once_with("TEST123")


class TestIsAvailable:
    """Tests for is_available method."""

    def test_unknown_device_returns_false(self, availability_service):
        """Test that unknown device returns False."""
        assert availability_service.is_available("UNKNOWN") is False

    @pytest.mark.asyncio
    async def test_tracked_available_device_returns_true(self, availability_service):
        """Test that tracked available device returns True."""
        await availability_service.mark_device_seen("TEST123")
        assert availability_service.is_available("TEST123") is True

    def test_unavailable_device_returns_false(self, availability_service):
        """Test that unavailable device returns False."""
        availability_service._devices["TEST123"] = DeviceStatus(
            serial="TEST123",
            is_available=False,
        )
        assert availability_service.is_available("TEST123") is False


class TestGetLastSeen:
    """Tests for get_last_seen method."""

    def test_unknown_device_returns_none(self, availability_service):
        """Test that unknown device returns None."""
        assert availability_service.get_last_seen("UNKNOWN") is None

    @pytest.mark.asyncio
    async def test_returns_last_seen_time(self, availability_service):
        """Test that last_seen time is returned."""
        await availability_service.mark_device_seen("TEST123")
        last_seen = availability_service.get_last_seen("TEST123")
        assert isinstance(last_seen, datetime)


class TestGetAllStatuses:
    """Tests for get_all_statuses method."""

    def test_empty_when_no_devices(self, availability_service):
        """Test empty dict when no devices tracked."""
        assert availability_service.get_all_statuses() == {}

    @pytest.mark.asyncio
    async def test_returns_all_device_statuses(self, availability_service):
        """Test that all device statuses are returned."""
        await availability_service.mark_device_seen("DEVICE1")
        await availability_service.mark_device_seen("DEVICE2")

        statuses = availability_service.get_all_statuses()
        assert "DEVICE1" in statuses
        assert "DEVICE2" in statuses
        assert statuses["DEVICE1"]["is_available"] is True
        assert "last_seen" in statuses["DEVICE1"]


class TestMarkDeviceUnavailable:
    """Tests for _mark_device_unavailable method."""

    @pytest.mark.asyncio
    async def test_marks_device_unavailable(self, availability_service):
        """Test that device is marked unavailable."""
        await availability_service.mark_device_seen("TEST123")
        await availability_service._mark_device_unavailable("TEST123")
        assert availability_service._devices["TEST123"].is_available is False

    @pytest.mark.asyncio
    async def test_notifies_integration_manager(self, availability_service):
        """Test that integration manager is notified."""
        mock_integration_manager = AsyncMock()
        availability_service.set_integration_manager(mock_integration_manager)

        await availability_service.mark_device_seen("TEST123")
        await availability_service._mark_device_unavailable("TEST123")

        mock_integration_manager.on_device_disconnected.assert_called_once_with("TEST123")

    @pytest.mark.asyncio
    async def test_unknown_device_is_ignored(self, availability_service):
        """Test that unknown device is ignored."""
        # Should not raise
        await availability_service._mark_device_unavailable("UNKNOWN")

    @pytest.mark.asyncio
    async def test_already_unavailable_not_notified_again(self, availability_service):
        """Test that already unavailable device doesn't trigger notification."""
        mock_integration_manager = AsyncMock()
        availability_service.set_integration_manager(mock_integration_manager)

        availability_service._devices["TEST123"] = DeviceStatus(
            serial="TEST123",
            is_available=False,
        )

        await availability_service._mark_device_unavailable("TEST123")
        mock_integration_manager.on_device_disconnected.assert_not_called()


class TestSetIntegrationManager:
    """Tests for set_integration_manager method."""

    def test_sets_integration_manager(self, availability_service):
        """Test that integration manager is set."""
        mock_manager = MagicMock()
        availability_service.set_integration_manager(mock_manager)
        assert availability_service._integration_manager is mock_manager
