"""Tests for device state service."""

from datetime import datetime

import pytest

from nolongerevil.lib.types import DeviceObject
from nolongerevil.services.device_state_service import DeviceStateService


class TestDeviceStateService:
    """Tests for DeviceStateService class."""

    @pytest.mark.asyncio
    async def test_upsert_and_get_object(self, state_service: DeviceStateService):
        """Test inserting and retrieving an object."""
        obj = DeviceObject(
            serial="TEST12345678",
            object_key="device.TEST12345678",
            object_revision=1,
            object_timestamp=1234567890,
            value={"target_temperature": 21.0},
            updated_at=datetime.utcnow(),
        )

        await state_service.upsert_object(obj)
        retrieved = state_service.get_object("TEST12345678", "device.TEST12345678")

        assert retrieved is not None
        assert retrieved.value["target_temperature"] == 21.0

    @pytest.mark.asyncio
    async def test_merge_object_values(self, state_service: DeviceStateService):
        """Test merging values into an existing object."""
        # Create initial object
        obj = DeviceObject(
            serial="TEST12345678",
            object_key="device.TEST12345678",
            object_revision=1,
            object_timestamp=1234567890,
            value={"target_temperature": 21.0, "mode": "heat"},
            updated_at=datetime.utcnow(),
        )
        await state_service.upsert_object(obj)

        # Merge new values
        updated = await state_service.merge_object_values(
            serial="TEST12345678",
            object_key="device.TEST12345678",
            values={"target_temperature": 22.0, "humidity": 50},
            revision=2,
            timestamp=1234567891,
        )

        assert updated.value["target_temperature"] == 22.0
        assert updated.value["mode"] == "heat"  # Preserved
        assert updated.value["humidity"] == 50  # Added

    @pytest.mark.asyncio
    async def test_get_all_serials(self, state_service: DeviceStateService):
        """Test getting all device serials."""
        obj1 = DeviceObject(
            serial="TEST12345678",
            object_key="device.TEST12345678",
            object_revision=1,
            object_timestamp=1234567890,
            value={"target_temperature": 21.0},
            updated_at=datetime.utcnow(),
        )
        obj2 = DeviceObject(
            serial="TEST87654321",
            object_key="device.TEST87654321",
            object_revision=1,
            object_timestamp=1234567890,
            value={"target_temperature": 22.0},
            updated_at=datetime.utcnow(),
        )

        await state_service.upsert_object(obj1)
        await state_service.upsert_object(obj2)

        serials = state_service.get_all_serials()

        assert len(serials) == 2
        assert "TEST12345678" in serials
        assert "TEST87654321" in serials

    @pytest.mark.asyncio
    async def test_has_updates_since(self, state_service: DeviceStateService):
        """Test checking for updates since a revision."""
        obj = DeviceObject(
            serial="TEST12345678",
            object_key="device.TEST12345678",
            object_revision=5,
            object_timestamp=1234567890,
            value={"target_temperature": 21.0},
            updated_at=datetime.utcnow(),
        )
        await state_service.upsert_object(obj)

        # Has updates (revision 5 > 0)
        updates = state_service.has_updates_since(
            "TEST12345678",
            {"device.TEST12345678": 0},
        )
        assert len(updates) == 1

        # No updates (already at revision 5)
        updates = state_service.has_updates_since(
            "TEST12345678",
            {"device.TEST12345678": 5},
        )
        assert len(updates) == 0

        # No updates (ahead of revision 5)
        updates = state_service.has_updates_since(
            "TEST12345678",
            {"device.TEST12345678": 10},
        )
        assert len(updates) == 0

    @pytest.mark.asyncio
    async def test_delete_object(self, state_service: DeviceStateService):
        """Test deleting an object."""
        obj = DeviceObject(
            serial="TEST12345678",
            object_key="device.TEST12345678",
            object_revision=1,
            object_timestamp=1234567890,
            value={"target_temperature": 21.0},
            updated_at=datetime.utcnow(),
        )
        await state_service.upsert_object(obj)

        result = await state_service.delete_object("TEST12345678", "device.TEST12345678")

        assert result is True
        assert state_service.get_object("TEST12345678", "device.TEST12345678") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_object(self, state_service: DeviceStateService):
        """Test deleting a non-existent object returns False."""
        result = await state_service.delete_object("NONEXISTENT", "device.NONEXISTENT")
        assert result is False
