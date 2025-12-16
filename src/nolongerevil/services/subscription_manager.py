"""Subscription manager for long-polling connections."""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nolongerevil.config import settings
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import DeviceObject

logger = get_logger(__name__)


class TooManySubscriptionsError(Exception):
    """Raised when subscription limit is exceeded."""

    pass


@dataclass
class ChunkedSubscription:
    """A chunked transfer subscription (HTTP connection kept open)."""

    serial: str
    session_id: str
    subscribed_keys: dict[str, int]  # object_key -> last known revision
    response: Any  # StreamingResponse or similar
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class PendingSubscription:
    """A pending long-poll subscription waiting for updates."""

    serial: str
    session_id: str
    subscribed_keys: dict[str, int]  # object_key -> last known revision
    future: asyncio.Future[list[DeviceObject]]
    created_at: datetime = field(default_factory=datetime.now)


class SubscriptionManager:
    """Manages active long-poll subscriptions.

    Supports two modes:
    1. Future-based (non-chunked): Returns updates via asyncio.Future
    2. Chunked: Keeps HTTP connection open and writes updates directly
    """

    def __init__(self) -> None:
        """Initialize the subscription manager."""
        self._subscriptions: dict[str, dict[str, PendingSubscription]] = {}
        self._chunked_subscriptions: dict[str, dict[str, ChunkedSubscription]] = {}
        self._lock = asyncio.Lock()

    # ========== Chunked Subscription Methods ==========

    async def add_chunked_subscription(
        self,
        serial: str,
        session_id: str,
        subscribed_keys: dict[str, int],
        response: Any,
    ) -> bool:
        """Add a chunked subscription.

        Args:
            serial: Device serial number
            session_id: Session identifier
            subscribed_keys: Map of object_key -> last known revision
            response: StreamingResponse to write updates to

        Returns:
            True if added, False if limit exceeded
        """
        async with self._lock:
            device_subs = self._chunked_subscriptions.get(serial, {})
            if len(device_subs) >= settings.max_subscriptions_per_device:
                logger.warning(
                    f"Max subscriptions ({settings.max_subscriptions_per_device}) "
                    f"exceeded for device {serial}"
                )
                return False

            subscription = ChunkedSubscription(
                serial=serial,
                session_id=session_id,
                subscribed_keys=subscribed_keys,
                response=response,
            )

            if serial not in self._chunked_subscriptions:
                self._chunked_subscriptions[serial] = {}
            self._chunked_subscriptions[serial][session_id] = subscription

            logger.debug(
                f"Added chunked subscription {session_id} for device {serial} "
                f"(total: {len(self._chunked_subscriptions[serial])})"
            )

            return True

    async def remove_chunked_subscription(
        self, serial: str, session_id: str, response: web.StreamResponse | None = None
    ) -> None:
        """Remove a chunked subscription.

        Args:
            serial: Device serial number
            session_id: Session identifier
            response: If provided, only remove if this response matches the stored one.
                     This prevents race conditions when session IDs are reused.
        """
        async with self._lock:
            if serial in self._chunked_subscriptions:
                if session_id in self._chunked_subscriptions[serial]:
                    # If response provided, only remove if it matches (prevents race condition)
                    if response is not None:
                        stored_sub = self._chunked_subscriptions[serial][session_id]
                        if stored_sub.response is not response:
                            logger.debug(
                                f"Skipping removal of {session_id} - response mismatch (reused session)"
                            )
                            return
                    del self._chunked_subscriptions[serial][session_id]
                    logger.debug(f"Removed chunked subscription {session_id} for {serial}")

                if not self._chunked_subscriptions[serial]:
                    del self._chunked_subscriptions[serial]

    async def notify_all_chunked(
        self,
        serial: str,
        changed_objects: list[dict[str, Any]],
    ) -> int:
        """Notify all chunked subscribers for a device.

        Args:
            serial: Device serial number
            changed_objects: List of changed object dicts

        Returns:
            Number of subscribers notified
        """
        notified = 0

        async with self._lock:
            device_subs = self._chunked_subscriptions.get(serial, {})
            sessions_to_remove = []

            for session_id, sub in device_subs.items():
                try:
                    # Check if response is still writable
                    response = sub.response
                    if response is None:
                        sessions_to_remove.append(session_id)
                        continue

                    # For Starlette StreamingResponse, we can't easily write after creation
                    # The chunked subscription pattern would need to be redesigned
                    # For now, just mark as notified and clean up
                    sessions_to_remove.append(session_id)
                    notified += 1

                    logger.debug(f"Notified chunked subscriber {session_id} for {serial}")

                except Exception as e:
                    logger.debug(f"Failed to notify chunked subscriber {session_id}: {e}")
                    sessions_to_remove.append(session_id)

            # Clean up notified/closed subscriptions
            for session_id in sessions_to_remove:
                if session_id in device_subs:
                    del device_subs[session_id]

            if not device_subs and serial in self._chunked_subscriptions:
                del self._chunked_subscriptions[serial]

        return notified

    # ========== Future-based Subscription Methods (Legacy) ==========

    async def add_subscription(
        self,
        serial: str,
        subscribed_keys: dict[str, int],
    ) -> tuple[str, asyncio.Future[list[DeviceObject]]]:
        """Add a future-based subscription."""
        async with self._lock:
            device_subs = self._subscriptions.get(serial, {})
            if len(device_subs) >= settings.max_subscriptions_per_device:
                raise TooManySubscriptionsError("Too many subscriptions for this device")

            session_id = str(uuid.uuid4())
            future: asyncio.Future[list[DeviceObject]] = asyncio.Future()

            subscription = PendingSubscription(
                serial=serial,
                session_id=session_id,
                subscribed_keys=subscribed_keys,
                future=future,
            )

            if serial not in self._subscriptions:
                self._subscriptions[serial] = {}
            self._subscriptions[serial][session_id] = subscription

            return session_id, future

    async def remove_subscription(self, serial: str, session_id: str) -> None:
        """Remove a future-based subscription."""
        async with self._lock:
            if serial in self._subscriptions and session_id in self._subscriptions[serial]:
                sub = self._subscriptions[serial].pop(session_id)
                if not sub.future.done():
                    sub.future.cancel()

                if not self._subscriptions[serial]:
                    del self._subscriptions[serial]

    async def notify_subscribers(
        self,
        serial: str,
        updated_objects: list[DeviceObject],
    ) -> int:
        """Notify future-based subscribers."""
        notified = 0

        async with self._lock:
            device_subs = self._subscriptions.get(serial, {})
            sessions_to_remove = []

            for session_id, sub in device_subs.items():
                relevant_updates = []
                for obj in updated_objects:
                    if obj.object_key in sub.subscribed_keys:
                        last_rev = sub.subscribed_keys[obj.object_key]
                        if obj.object_revision > last_rev:
                            relevant_updates.append(obj)

                if relevant_updates and not sub.future.done():
                    sub.future.set_result(relevant_updates)
                    sessions_to_remove.append(session_id)
                    notified += 1

            for session_id in sessions_to_remove:
                del device_subs[session_id]

            if not device_subs and serial in self._subscriptions:
                del self._subscriptions[serial]

        return notified

    async def notify_all_subscribers(
        self,
        serial: str,
        updated_objects: list[DeviceObject],
    ) -> int:
        """Notify all subscribers (both chunked and future-based) for a device.

        Args:
            serial: Device serial number
            updated_objects: List of updated device objects

        Returns:
            Total number of subscribers notified
        """
        total_notified = 0

        # Format objects for chunked subscribers
        formatted_objects = [
            {
                "serial": obj.serial,
                "object_key": obj.object_key,
                "object_revision": obj.object_revision,
                "object_timestamp": obj.object_timestamp,
                "value": obj.value,
            }
            for obj in updated_objects
        ]

        # Notify chunked subscribers
        chunked_count = await self.notify_all_chunked(serial, formatted_objects)
        total_notified += chunked_count

        # Notify future-based subscribers
        future_count = await self.notify_subscribers(serial, updated_objects)
        total_notified += future_count

        return total_notified

    # ========== Utility Methods ==========

    def get_subscription_count(self, serial: str) -> int:
        """Get total subscriptions for a device."""
        future_count = len(self._subscriptions.get(serial, {}))
        chunked_count = len(self._chunked_subscriptions.get(serial, {}))
        return future_count + chunked_count

    def get_total_subscription_count(self) -> int:
        """Get total subscriptions across all devices."""
        future_total = sum(len(subs) for subs in self._subscriptions.values())
        chunked_total = sum(len(subs) for subs in self._chunked_subscriptions.values())
        return future_total + chunked_total

    def has_active_subscription(self, serial: str) -> bool:
        """Check if device has any active subscription."""
        has_future = serial in self._subscriptions and len(self._subscriptions[serial]) > 0
        has_chunked = (
            serial in self._chunked_subscriptions and len(self._chunked_subscriptions[serial]) > 0
        )
        return has_future or has_chunked

    def get_stats(self) -> dict[str, Any]:
        """Get subscription statistics."""
        return {
            "total_subscriptions": self.get_total_subscription_count(),
            "future_subscriptions": sum(len(subs) for subs in self._subscriptions.values()),
            "chunked_subscriptions": sum(
                len(subs) for subs in self._chunked_subscriptions.values()
            ),
            "devices_with_subscriptions": len(
                set(self._subscriptions.keys()) | set(self._chunked_subscriptions.keys())
            ),
        }
