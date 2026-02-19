"""Subscription manager for long-polling connections.

Long-poll subscriptions hold the HTTP connection open without sending any response.
The notification queue wakes the handler when data arrives. When data is pushed
to the queue, the transport handler sends a complete HTTP response and closes.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nolongerevil.config import settings
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import DeviceObject

logger = get_logger(__name__)

# If a subscription ended within this window, the next subscribe is a "re-subscribe"
RESUBSCRIBE_WINDOW_SECONDS = 5.0


@dataclass
class LongPollSubscription:
    """A long-poll subscription for server-push to devices.

    The transport layer holds the HTTP connection open (chunked headers sent,
    body pending) and waits on the notify_queue. When data is pushed to the
    queue, the transport sends the body and closes.

    Each subscription has a unique server-generated ID. The device's session_id
    is preserved for logging but not used as a key - devices reuse session IDs
    across requests, which would cause race conditions if used for keying.
    """

    id: str  # Server-generated UUID (unique per subscription)
    serial: str
    session_id: str  # Device-provided, for logging only
    notify_queue: asyncio.Queue[list[dict[str, Any]]] = field(default_factory=asyncio.Queue)
    created_at: datetime = field(default_factory=datetime.now)


class SubscriptionManager:
    """Manages active long-poll subscriptions.

    Long-poll subscriptions:
    - Connection is held open without sending HTTP response
    - Notification queue wakes the transport handler when data arrives
    - Transport sends complete response and closes
    """

    def __init__(self) -> None:
        """Initialize the subscription manager."""
        self._long_poll_subscriptions: dict[str, dict[str, LongPollSubscription]] = {}
        self._last_subscription_end: dict[str, float] = {}  # serial -> timestamp
        self._pending_pushes: dict[str, list[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    # ========== Long-Poll Subscription Methods ==========

    async def add_long_poll_subscription(
        self,
        serial: str,
        session_id: str,
    ) -> LongPollSubscription | None:
        """Add a long-poll subscription (connection held without response).

        Returns the subscription object for the caller to use directly. The
        subscription is keyed by a server-generated UUID, not the device's
        session_id, to avoid race conditions when the device reuses session IDs.

        Args:
            serial: Device serial number
            session_id: Device's session identifier (for logging only)

        Returns:
            LongPollSubscription if added, None if limit exceeded
        """
        async with self._lock:
            device_subs = self._long_poll_subscriptions.get(serial, {})
            if len(device_subs) >= settings.max_subscriptions_per_device:
                logger.warning(
                    f"Max subscriptions ({settings.max_subscriptions_per_device}) "
                    f"exceeded for device {serial}"
                )
                return None

            subscription = LongPollSubscription(
                id=str(uuid.uuid4()),
                serial=serial,
                session_id=session_id,
            )

            if serial not in self._long_poll_subscriptions:
                self._long_poll_subscriptions[serial] = {}
            self._long_poll_subscriptions[serial][subscription.id] = subscription

            # Replay any pending pushes that failed delivery on the previous connection
            pending = self._pending_pushes.pop(serial, None)
            if pending:
                subscription.notify_queue.put_nowait(pending)
                logger.info(
                    f"Replayed {len(pending)} pending object(s) to new subscription "
                    f"{subscription.id} for {serial}"
                )

            total = len(self._long_poll_subscriptions[serial])
            logger.debug(
                f"Added subscription {subscription.id} for {serial} "
                f"(session={session_id}, total={total})"
            )

            # Detect stale connections: if total > 1, previous subscribe connections
            # are still being held open but the device has already reconnected —
            # meaning those connections died silently (e.g. NAT dropped them).
            if total > 1:
                now = datetime.now()
                for old_id, old_sub in self._long_poll_subscriptions[serial].items():
                    if old_id != subscription.id:
                        age = (now - old_sub.created_at).total_seconds()
                        logger.warning(
                            f"Stale subscription {old_id} for {serial}: "
                            f"held for {age:.0f}s before device reconnected"
                        )

            return subscription

    async def remove_long_poll_subscription(self, subscription: LongPollSubscription) -> None:
        """Remove a specific subscription by its unique ID.

        Args:
            subscription: The subscription object to remove
        """
        async with self._lock:
            device_subs = self._long_poll_subscriptions.get(subscription.serial, {})
            if subscription.id in device_subs:
                del device_subs[subscription.id]
                self._last_subscription_end[subscription.serial] = time.monotonic()
                logger.debug(f"Removed subscription {subscription.id} for {subscription.serial}")

            if not device_subs and subscription.serial in self._long_poll_subscriptions:
                del self._long_poll_subscriptions[subscription.serial]

    async def store_pending_push(self, serial: str, objects: list[dict[str, Any]]) -> None:
        """Buffer objects that failed delivery due to a broken connection.

        The next call to add_long_poll_subscription for this serial will
        replay these objects to the new subscription's queue.
        """
        async with self._lock:
            existing = self._pending_pushes.get(serial, [])
            existing.extend(objects)
            self._pending_pushes[serial] = existing
            logger.info(
                f"Buffered {len(objects)} pending object(s) for {serial} "
                f"(total pending: {len(existing)})"
            )

    async def notify_long_poll_subscribers(
        self,
        serial: str,
        changed_objects: list[dict[str, Any]],
    ) -> int:
        """Notify all long-poll subscribers for a device.

        This puts data on each subscription's queue. The transport layer
        (which is waiting on the queue) will wake up, send the HTTP response,
        and close the connection.

        Args:
            serial: Device serial number
            changed_objects: List of changed object dicts

        Returns:
            Number of subscribers notified (queued)
        """
        notified = 0

        async with self._lock:
            device_subs = self._long_poll_subscriptions.get(serial, {})

            for sub_id, sub in device_subs.items():
                try:
                    # Put data on queue - transport layer will read and respond
                    sub.notify_queue.put_nowait(changed_objects)
                    notified += 1
                    logger.debug(f"Queued notification for subscription {sub_id}")

                except Exception as e:
                    logger.debug(f"Failed to queue notification for {sub_id}: {e}")

        return notified

    async def notify_subscribers_with_objects(
        self,
        serial: str,
        updated_objects: list[DeviceObject],
    ) -> int:
        """Notify subscribers with DeviceObject list (formats them).

        Args:
            serial: Device serial number
            updated_objects: List of DeviceObject instances

        Returns:
            Total number of subscribers notified
        """
        if not updated_objects:
            return 0

        # IMPORTANT: object_revision and object_timestamp MUST come before object_key
        # Note: serial omitted per spec - device extracts from object_key
        formatted_objects = [
            {
                "object_revision": obj.object_revision,
                "object_timestamp": obj.object_timestamp,
                "object_key": obj.object_key,
                "value": obj.value,
            }
            for obj in updated_objects
        ]
        return await self.notify_long_poll_subscribers(serial, formatted_objects)

    async def notify_subscribers_with_dicts(
        self,
        serial: str,
        formatted_objects: list[dict[str, Any]],
    ) -> int:
        """Notify subscribers with pre-formatted dicts.

        Args:
            serial: Device serial number
            formatted_objects: List of pre-formatted object dicts

        Returns:
            Total number of subscribers notified
        """
        if not formatted_objects:
            return 0
        return await self.notify_long_poll_subscribers(serial, formatted_objects)

    async def notify_all_subscribers(
        self,
        serial: str,
        updated_objects: list[DeviceObject] | list[dict[str, Any]],
    ) -> int:
        """Notify all subscribers for a device.

        Args:
            serial: Device serial number
            updated_objects: List of DeviceObject instances or pre-formatted dicts

        Returns:
            Total number of subscribers notified
        """
        if not updated_objects:
            return 0

        if isinstance(updated_objects[0], DeviceObject):
            return await self.notify_subscribers_with_objects(serial, updated_objects)  # type: ignore
        return await self.notify_subscribers_with_dicts(serial, updated_objects)  # type: ignore

    # ========== Utility Methods ==========

    def get_subscription_count(self, serial: str) -> int:
        """Get total subscriptions for a device."""
        return len(self._long_poll_subscriptions.get(serial, {}))

    def get_total_subscription_count(self) -> int:
        """Get total subscriptions across all devices."""
        return sum(len(subs) for subs in self._long_poll_subscriptions.values())

    def has_active_subscription(self, serial: str) -> bool:
        """Check if device has any active subscription."""
        return (
            serial in self._long_poll_subscriptions
            and len(self._long_poll_subscriptions[serial]) > 0
        )

    def is_resubscribe(self, serial: str) -> bool:
        """Check if this is a re-subscribe (recent subscription ended).

        Returns True if a subscription for this device ended within the
        re-subscribe window (typically 5 seconds). This indicates the device
        is in a normal cycle and we can use standard timing.

        Returns False if this is a fresh subscribe (no recent history).
        """
        last_end = self._last_subscription_end.get(serial)
        if last_end is None:
            return False
        return (time.monotonic() - last_end) < RESUBSCRIBE_WINDOW_SECONDS

    def get_stats(self) -> dict[str, Any]:
        """Get subscription statistics."""
        return {
            "total_subscriptions": self.get_total_subscription_count(),
            "devices_with_subscriptions": len(self._long_poll_subscriptions),
        }
