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
            updated_at=datetime.now(),
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
            updated_at=datetime.now(),
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
            updated_at=datetime.now(),
        )
        obj2 = DeviceObject(
            serial="TEST87654321",
            object_key="device.TEST87654321",
            object_revision=1,
            object_timestamp=1234567890,
            value={"target_temperature": 22.0},
            updated_at=datetime.now(),
        )

        await state_service.upsert_object(obj1)
        await state_service.upsert_object(obj2)

        serials = state_service.get_all_serials()

        assert len(serials) == 2
        assert "TEST12345678" in serials
        assert "TEST87654321" in serials

    @pytest.mark.asyncio
    async def test_get_all_serials_excludes_mac_alias_records(self, state_service: DeviceStateService):
        """mac_alias.<mac> bookkeeping records aren't real devices."""
        device_obj = DeviceObject(
            serial="TEST12345678",
            object_key="device.TEST12345678",
            object_revision=1,
            object_timestamp=1234567890,
            value={"target_temperature": 21.0},
            updated_at=datetime.now(),
        )
        alias_obj = DeviceObject(
            serial="mac_alias.11b2334455d6",
            object_key="mac_alias",
            object_revision=1,
            object_timestamp=1234567890,
            value={"serial": "TEST12345678"},
            updated_at=datetime.now(),
        )

        await state_service.upsert_object(device_obj)
        await state_service.upsert_object(alias_obj)

        serials = state_service.get_all_serials()

        assert serials == ["TEST12345678"]

    @pytest.mark.asyncio
    async def test_get_object_by_prefix_finds_differently_cased_key(
        self, state_service: DeviceStateService
    ):
        """get_object_by_prefix finds an object regardless of the casing used
        in the rest of its object_key (e.g. MAC-alias-migrated buckets)."""
        obj = DeviceObject(
            serial="TEST12345678",
            object_key="schedule.test12345678",
            object_revision=1,
            object_timestamp=1234567890,
            value={"ver": 2},
            updated_at=datetime.now(),
        )
        await state_service.upsert_object(obj)

        found = state_service.get_object_by_prefix("TEST12345678", "schedule.")

        assert found is not None
        assert found.object_key == "schedule.test12345678"

    @pytest.mark.asyncio
    async def test_get_object_by_prefix_no_match_returns_none(
        self, state_service: DeviceStateService
    ):
        """get_object_by_prefix returns None when no object_key matches the prefix."""
        obj = DeviceObject(
            serial="TEST12345678",
            object_key="device.test12345678",
            object_revision=1,
            object_timestamp=1234567890,
            value={},
            updated_at=datetime.now(),
        )
        await state_service.upsert_object(obj)

        assert state_service.get_object_by_prefix("TEST12345678", "schedule.") is None

    @pytest.mark.asyncio
    async def test_has_updates_since(self, state_service: DeviceStateService):
        """Test checking for updates since a revision."""
        obj = DeviceObject(
            serial="TEST12345678",
            object_key="device.TEST12345678",
            object_revision=5,
            object_timestamp=1234567890,
            value={"target_temperature": 21.0},
            updated_at=datetime.now(),
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
            updated_at=datetime.now(),
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
