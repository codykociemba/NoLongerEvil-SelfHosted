"""Device state service with in-memory caching."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import DeviceObject, DeviceStateChange

if TYPE_CHECKING:
    from nolongerevil.integrations.integration_manager import IntegrationManager
    from nolongerevil.services.abstract_device_state_manager import AbstractDeviceStateManager

logger = get_logger(__name__)


class DeviceStateService:
    """Device state service with in-memory caching layer.

    Provides low-latency reads through in-memory cache while
    persisting to the underlying storage backend.
    """

    def __init__(self, storage: "AbstractDeviceStateManager") -> None:
        """Initialize the device state service.

        Args:
            storage: Backend storage implementation
        """
        self._storage = storage
        self._cache: dict[str, dict[str, DeviceObject]] = {}  # serial -> object_key -> object
        self._integration_manager: IntegrationManager | None = None

    def set_integration_manager(self, manager: "IntegrationManager") -> None:
        """Set the integration manager for state change notifications.

        Args:
            manager: Integration manager instance
        """
        self._integration_manager = manager

    async def initialize(self) -> None:
        """Initialize the service and load cache from storage."""
        await self._storage.initialize()
        await self._load_cache()
        logger.info("Device state service initialized")

    async def close(self) -> None:
        """Close the service and storage backend."""
        await self._storage.close()
        self._cache.clear()
        logger.info("Device state service closed")

    async def _load_cache(self) -> None:
        """Load all objects from storage into cache."""
        objects = await self._storage.get_all_objects()
        for obj in objects:
            if obj.serial not in self._cache:
                self._cache[obj.serial] = {}
            self._cache[obj.serial][obj.object_key] = obj
        logger.info(f"Loaded {len(objects)} objects into cache")

    def get_object(self, serial: str, object_key: str) -> DeviceObject | None:
        """Get a device object from cache.

        Args:
            serial: Device serial number
            object_key: Object key (e.g., "device.serial", "shared.serial")

        Returns:
            Device object or None if not found
        """
        return self._cache.get(serial, {}).get(object_key)

    def get_objects_by_serial(self, serial: str) -> list[DeviceObject]:
        """Get all objects for a device from cache.

        Args:
            serial: Device serial number

        Returns:
            List of device objects
        """
        return list(self._cache.get(serial, {}).values())

    def get_all_objects(self) -> list[DeviceObject]:
        """Get all device objects from cache.

        Returns:
            List of all device objects
        """
        result: list[DeviceObject] = []
        for serial_objects in self._cache.values():
            result.extend(serial_objects.values())
        return result

    def get_all_serials(self) -> list[str]:
        """Get all known device serials.

        Returns:
            List of device serial numbers
        """
        return list(self._cache.keys())

    async def delete_device(self, serial: str) -> int:
        """Delete all objects for a device.

        Args:
            serial: Device serial number

        Returns:
            Number of objects deleted
        """
        if serial not in self._cache:
            return 0

        # Get count before deleting
        deleted_count = len(self._cache[serial])

        # Delete from cache
        del self._cache[serial]

        # Delete from storage
        await self._storage.delete_device(serial)

        logger.info(f"Deleted device {serial} ({deleted_count} objects)")
        return deleted_count

    async def upsert_object(self, obj: DeviceObject) -> DeviceObject | None:
        """Insert or update a device object.

        Args:
            obj: Device object to upsert

        Returns:
            Previous value if existed, None otherwise
        """
        # Get old value for change notification
        old_obj = self.get_object(obj.serial, obj.object_key)
        old_value = old_obj.value if old_obj else None

        # Update cache
        if obj.serial not in self._cache:
            self._cache[obj.serial] = {}
        self._cache[obj.serial][obj.object_key] = obj

        # Persist to storage
        await self._storage.upsert_object(obj)

        # Notify integration manager of state change
        if self._integration_manager:
            # Compute changed fields
            changed_fields: list[str] = []
            if old_value is None:
                changed_fields = list(obj.value.keys())
            else:
                for key in obj.value:
                    if key not in old_value or obj.value[key] != old_value[key]:
                        changed_fields.append(key)

            change = DeviceStateChange(
                serial=obj.serial,
                object_key=obj.object_key,
                old_value=old_value,
                new_value=obj.value,
                changed_fields=changed_fields,
                timestamp=obj.updated_at,
            )
            await self._integration_manager.on_device_state_change(change)

        logger.debug(f"Upserted object {obj.object_key} for device {obj.serial}")
        return old_obj

    async def merge_object_values(
        self,
        serial: str,
        object_key: str,
        values: dict[str, Any],
        revision: int,
        timestamp: int,
    ) -> DeviceObject:
        """Merge new values into an existing object.

        Args:
            serial: Device serial number
            object_key: Object key
            values: Values to merge
            revision: New object revision
            timestamp: New object timestamp

        Returns:
            Updated device object
        """
        existing = self.get_object(serial, object_key)
        merged_values = {**existing.value, **values} if existing else values

        obj = DeviceObject(
            serial=serial,
            object_key=object_key,
            object_revision=revision,
            object_timestamp=timestamp,
            value=merged_values,
            updated_at=datetime.now(),
        )

        await self.upsert_object(obj)
        return obj

    async def delete_object(self, serial: str, object_key: str) -> bool:
        """Delete a device object.

        Args:
            serial: Device serial number
            object_key: Object key

        Returns:
            True if deleted, False if not found
        """
        # Remove from cache
        if serial in self._cache and object_key in self._cache[serial]:
            del self._cache[serial][object_key]
            if not self._cache[serial]:
                del self._cache[serial]

            # Remove from storage
            await self._storage.delete_object(serial, object_key)
            logger.debug(f"Deleted object {object_key} for device {serial}")
            return True

        return False

    def has_updates_since(
        self,
        serial: str,
        subscribed_keys: dict[str, int],
    ) -> list[DeviceObject]:
        """Check if there are updates since the given revisions.

        Args:
            serial: Device serial number
            subscribed_keys: Map of object_key -> last known revision

        Returns:
            List of objects that have been updated
        """
        updates = []
        device_objects = self._cache.get(serial, {})

        for object_key, last_revision in subscribed_keys.items():
            obj = device_objects.get(object_key)
            if obj and obj.object_revision > last_revision:
                updates.append(obj)

        return updates

    @property
    def storage(self) -> "AbstractDeviceStateManager":
        """Get the underlying storage backend."""
        return self._storage
