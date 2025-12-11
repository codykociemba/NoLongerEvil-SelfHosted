"""Tests for subscription manager."""

from datetime import datetime

import pytest

from nolongerevil.lib.types import DeviceObject
from nolongerevil.services.subscription_manager import SubscriptionManager


class TestSubscriptionManager:
    """Tests for SubscriptionManager class."""

    @pytest.mark.asyncio
    async def test_add_subscription(self, subscription_manager: SubscriptionManager):
        """Test adding a subscription."""
        session_id, future = await subscription_manager.add_subscription(
            "TEST12345678",
            {"device.TEST12345678": 0},
        )

        assert session_id is not None
        assert not future.done()
        assert subscription_manager.get_subscription_count("TEST12345678") == 1

    @pytest.mark.asyncio
    async def test_remove_subscription(self, subscription_manager: SubscriptionManager):
        """Test removing a subscription."""
        session_id, future = await subscription_manager.add_subscription(
            "TEST12345678",
            {"device.TEST12345678": 0},
        )

        await subscription_manager.remove_subscription("TEST12345678", session_id)

        assert subscription_manager.get_subscription_count("TEST12345678") == 0

    @pytest.mark.asyncio
    async def test_notify_subscribers(self, subscription_manager: SubscriptionManager):
        """Test notifying subscribers."""
        session_id, future = await subscription_manager.add_subscription(
            "TEST12345678",
            {"device.TEST12345678": 0},
        )

        updated_obj = DeviceObject(
            serial="TEST12345678",
            object_key="device.TEST12345678",
            object_revision=1,
            object_timestamp=1234567890,
            value={"target_temperature": 21.0},
            updated_at=datetime.now(),
        )

        notified = await subscription_manager.notify_subscribers(
            "TEST12345678",
            [updated_obj],
        )

        assert notified == 1
        assert future.done()

        result = await future
        assert len(result) == 1
        assert result[0].object_key == "device.TEST12345678"

    @pytest.mark.asyncio
    async def test_notify_only_relevant_keys(self, subscription_manager: SubscriptionManager):
        """Test that only subscribed keys are sent."""
        session_id, future = await subscription_manager.add_subscription(
            "TEST12345678",
            {"device.TEST12345678": 0},  # Only subscribed to device
        )

        device_obj = DeviceObject(
            serial="TEST12345678",
            object_key="device.TEST12345678",
            object_revision=1,
            object_timestamp=1234567890,
            value={"target_temperature": 21.0},
            updated_at=datetime.now(),
        )
        shared_obj = DeviceObject(
            serial="TEST12345678",
            object_key="shared.TEST12345678",  # Not subscribed
            object_revision=1,
            object_timestamp=1234567890,
            value={"name": "Test"},
            updated_at=datetime.now(),
        )

        await subscription_manager.notify_subscribers(
            "TEST12345678",
            [device_obj, shared_obj],
        )

        result = await future
        assert len(result) == 1
        assert result[0].object_key == "device.TEST12345678"

    @pytest.mark.asyncio
    async def test_no_notification_if_revision_not_updated(
        self, subscription_manager: SubscriptionManager
    ):
        """Test that no notification is sent if revision hasn't changed."""
        session_id, future = await subscription_manager.add_subscription(
            "TEST12345678",
            {"device.TEST12345678": 5},  # Already at revision 5
        )

        updated_obj = DeviceObject(
            serial="TEST12345678",
            object_key="device.TEST12345678",
            object_revision=5,  # Same revision
            object_timestamp=1234567890,
            value={"target_temperature": 21.0},
            updated_at=datetime.now(),
        )

        notified = await subscription_manager.notify_subscribers(
            "TEST12345678",
            [updated_obj],
        )

        assert notified == 0
        assert not future.done()

    @pytest.mark.asyncio
    async def test_has_active_subscription(self, subscription_manager: SubscriptionManager):
        """Test checking for active subscriptions."""
        assert subscription_manager.has_active_subscription("TEST12345678") is False

        session_id, _ = await subscription_manager.add_subscription(
            "TEST12345678",
            {"device.TEST12345678": 0},
        )

        assert subscription_manager.has_active_subscription("TEST12345678") is True

        await subscription_manager.remove_subscription("TEST12345678", session_id)

        assert subscription_manager.has_active_subscription("TEST12345678") is False

    @pytest.mark.asyncio
    async def test_get_stats(self, subscription_manager: SubscriptionManager):
        """Test getting subscription statistics."""
        await subscription_manager.add_subscription(
            "TEST12345678",
            {"device.TEST12345678": 0},
        )
        await subscription_manager.add_subscription(
            "TEST12345678",
            {"shared.TEST12345678": 0},
        )
        await subscription_manager.add_subscription(
            "TEST87654321",
            {"device.TEST87654321": 0},
        )

        stats = subscription_manager.get_stats()

        assert stats["total_subscriptions"] == 3
        assert stats["devices_with_subscriptions"] == 2
        assert stats["future_subscriptions"] == 3  # All subscriptions are future-based
        assert stats["chunked_subscriptions"] == 0
