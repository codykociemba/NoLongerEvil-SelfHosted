"""Tests for subscription manager."""

import pytest

from nolongerevil.services.subscription_manager import SubscriptionManager


class TestSubscriptionManager:
    """Tests for SubscriptionManager class."""

    @pytest.mark.asyncio
    async def test_add_long_poll_subscription(self, subscription_manager: SubscriptionManager):
        """Test adding a long-poll subscription."""
        subscription = await subscription_manager.add_long_poll_subscription(
            "TEST12345678",
            "session_123",
        )

        assert subscription is not None
        assert subscription.serial == "TEST12345678"
        assert subscription.session_id == "session_123"
        assert subscription_manager.get_subscription_count("TEST12345678") == 1

    @pytest.mark.asyncio
    async def test_remove_long_poll_subscription(self, subscription_manager: SubscriptionManager):
        """Test removing a long-poll subscription."""
        subscription = await subscription_manager.add_long_poll_subscription(
            "TEST12345678",
            "session_123",
        )

        await subscription_manager.remove_long_poll_subscription(subscription)

        assert subscription_manager.get_subscription_count("TEST12345678") == 0

    @pytest.mark.asyncio
    async def test_notify_long_poll_subscribers(self, subscription_manager: SubscriptionManager):
        """Test notifying long-poll subscribers."""
        subscription = await subscription_manager.add_long_poll_subscription(
            "TEST12345678",
            "session_123",
        )

        changed_objects = [
            {
                "object_revision": 1,
                "object_timestamp": 1234567890,
                "object_key": "device.TEST12345678",
                "value": {"target_temperature": 21.0},
            }
        ]

        notified = await subscription_manager.notify_long_poll_subscribers(
            "TEST12345678",
            changed_objects,
        )

        assert notified == 1

        # Data should be in the queue
        queued_data = subscription.notify_queue.get_nowait()
        assert len(queued_data) == 1
        assert queued_data[0]["object_key"] == "device.TEST12345678"

    @pytest.mark.asyncio
    async def test_notify_multiple_subscribers(self, subscription_manager: SubscriptionManager):
        """Test notifying multiple subscribers for same device."""
        sub1 = await subscription_manager.add_long_poll_subscription(
            "TEST12345678",
            "session_1",
        )
        sub2 = await subscription_manager.add_long_poll_subscription(
            "TEST12345678",
            "session_2",
        )

        changed_objects = [{"object_key": "device.TEST12345678", "value": {}}]

        notified = await subscription_manager.notify_long_poll_subscribers(
            "TEST12345678",
            changed_objects,
        )

        assert notified == 2
        assert not sub1.notify_queue.empty()
        assert not sub2.notify_queue.empty()

    @pytest.mark.asyncio
    async def test_has_active_subscription(self, subscription_manager: SubscriptionManager):
        """Test checking for active subscriptions."""
        assert subscription_manager.has_active_subscription("TEST12345678") is False

        subscription = await subscription_manager.add_long_poll_subscription(
            "TEST12345678",
            "session_123",
        )

        assert subscription_manager.has_active_subscription("TEST12345678") is True

        await subscription_manager.remove_long_poll_subscription(subscription)

        assert subscription_manager.has_active_subscription("TEST12345678") is False

    @pytest.mark.asyncio
    async def test_get_stats(self, subscription_manager: SubscriptionManager):
        """Test getting subscription statistics."""
        await subscription_manager.add_long_poll_subscription(
            "TEST12345678",
            "session_1",
        )
        await subscription_manager.add_long_poll_subscription(
            "TEST12345678",
            "session_2",
        )
        await subscription_manager.add_long_poll_subscription(
            "TEST87654321",
            "session_3",
        )

        stats = subscription_manager.get_stats()

        assert stats["total_subscriptions"] == 3
        assert stats["devices_with_subscriptions"] == 2

    @pytest.mark.asyncio
    async def test_is_resubscribe(self, subscription_manager: SubscriptionManager):
        """Test detecting re-subscribe after recent subscription end."""
        # Initially no history
        assert subscription_manager.is_resubscribe("TEST12345678") is False

        # Add and remove a subscription
        subscription = await subscription_manager.add_long_poll_subscription(
            "TEST12345678",
            "session_123",
        )
        await subscription_manager.remove_long_poll_subscription(subscription)

        # Should now be detected as re-subscribe
        assert subscription_manager.is_resubscribe("TEST12345678") is True
