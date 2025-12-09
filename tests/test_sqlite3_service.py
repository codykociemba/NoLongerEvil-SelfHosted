"""Tests for SQLite3 service."""

from datetime import datetime, timedelta

import pytest

from nolongerevil.lib.types import (
    DeviceObject,
    DeviceOwner,
    EntryKey,
    UserInfo,
    WeatherData,
)
from nolongerevil.services.sqlite3_service import SQLite3Service


class TestSQLite3ServiceDeviceObjects:
    """Tests for device object operations."""

    @pytest.mark.asyncio
    async def test_upsert_and_get_object(self, sqlite_service: SQLite3Service):
        """Test inserting and retrieving an object."""
        obj = DeviceObject(
            serial="TEST12345678",
            object_key="device.TEST12345678",
            object_revision=1,
            object_timestamp=1234567890,
            value={"target_temperature": 21.0},
            updated_at=datetime.utcnow(),
        )

        await sqlite_service.upsert_object(obj)
        retrieved = await sqlite_service.get_object("TEST12345678", "device.TEST12345678")

        assert retrieved is not None
        assert retrieved.serial == "TEST12345678"
        assert retrieved.value["target_temperature"] == 21.0

    @pytest.mark.asyncio
    async def test_get_nonexistent_object(self, sqlite_service: SQLite3Service):
        """Test retrieving non-existent object returns None."""
        result = await sqlite_service.get_object("NONEXISTENT", "device.NONEXISTENT")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_objects_by_serial(self, sqlite_service: SQLite3Service):
        """Test retrieving all objects for a serial."""
        serial = "TEST12345678"

        obj1 = DeviceObject(
            serial=serial,
            object_key=f"device.{serial}",
            object_revision=1,
            object_timestamp=1234567890,
            value={"target_temperature": 21.0},
            updated_at=datetime.utcnow(),
        )
        obj2 = DeviceObject(
            serial=serial,
            object_key=f"shared.{serial}",
            object_revision=1,
            object_timestamp=1234567890,
            value={"name": "Living Room"},
            updated_at=datetime.utcnow(),
        )

        await sqlite_service.upsert_object(obj1)
        await sqlite_service.upsert_object(obj2)

        objects = await sqlite_service.get_objects_by_serial(serial)

        assert len(objects) == 2
        keys = [o.object_key for o in objects]
        assert f"device.{serial}" in keys
        assert f"shared.{serial}" in keys

    @pytest.mark.asyncio
    async def test_delete_object(self, sqlite_service: SQLite3Service):
        """Test deleting an object."""
        obj = DeviceObject(
            serial="TEST12345678",
            object_key="device.TEST12345678",
            object_revision=1,
            object_timestamp=1234567890,
            value={"target_temperature": 21.0},
            updated_at=datetime.utcnow(),
        )

        await sqlite_service.upsert_object(obj)
        result = await sqlite_service.delete_object("TEST12345678", "device.TEST12345678")

        assert result is True
        assert await sqlite_service.get_object("TEST12345678", "device.TEST12345678") is None


class TestSQLite3ServiceEntryKeys:
    """Tests for entry key operations."""

    @pytest.mark.asyncio
    async def test_create_and_get_entry_key(self, sqlite_service: SQLite3Service):
        """Test creating and retrieving an entry key."""
        now = datetime.utcnow()
        entry_key = EntryKey(
            code="ABC123",
            serial="TEST12345678",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )

        await sqlite_service.create_entry_key(entry_key)
        retrieved = await sqlite_service.get_entry_key("ABC123")

        assert retrieved is not None
        assert retrieved.serial == "TEST12345678"
        assert retrieved.claimed_by is None

    @pytest.mark.asyncio
    async def test_claim_entry_key(self, sqlite_service: SQLite3Service):
        """Test claiming an entry key."""
        now = datetime.utcnow()
        entry_key = EntryKey(
            code="ABC123",
            serial="TEST12345678",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )

        await sqlite_service.create_entry_key(entry_key)
        result = await sqlite_service.claim_entry_key("ABC123", "user_123")

        assert result is True

        retrieved = await sqlite_service.get_entry_key("ABC123")
        assert retrieved.claimed_by == "user_123"

    @pytest.mark.asyncio
    async def test_claim_expired_key_fails(self, sqlite_service: SQLite3Service):
        """Test claiming an expired key fails."""
        now = datetime.utcnow()
        entry_key = EntryKey(
            code="ABC123",
            serial="TEST12345678",
            created_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )

        await sqlite_service.create_entry_key(entry_key)
        result = await sqlite_service.claim_entry_key("ABC123", "user_123")

        assert result is False


class TestSQLite3ServiceUsers:
    """Tests for user operations."""

    @pytest.mark.asyncio
    async def test_create_and_get_user(self, sqlite_service: SQLite3Service):
        """Test creating and retrieving a user."""
        user = UserInfo(
            clerk_id="user_123",
            email="test@example.com",
            created_at=datetime.utcnow(),
        )

        await sqlite_service.create_user(user)
        retrieved = await sqlite_service.get_user("user_123")

        assert retrieved is not None
        assert retrieved.email == "test@example.com"

    @pytest.mark.asyncio
    async def test_get_user_by_email(self, sqlite_service: SQLite3Service):
        """Test retrieving user by email."""
        user = UserInfo(
            clerk_id="user_123",
            email="test@example.com",
            created_at=datetime.utcnow(),
        )

        await sqlite_service.create_user(user)
        retrieved = await sqlite_service.get_user_by_email("test@example.com")

        assert retrieved is not None
        assert retrieved.clerk_id == "user_123"


class TestSQLite3ServiceDeviceOwners:
    """Tests for device owner operations."""

    @pytest.mark.asyncio
    async def test_set_and_get_device_owner(self, sqlite_service: SQLite3Service):
        """Test setting and retrieving device owner."""
        owner = DeviceOwner(
            serial="TEST12345678",
            user_id="user_123",
            created_at=datetime.utcnow(),
        )

        await sqlite_service.set_device_owner(owner)
        retrieved = await sqlite_service.get_device_owner("TEST12345678")

        assert retrieved is not None
        assert retrieved.user_id == "user_123"

    @pytest.mark.asyncio
    async def test_get_user_devices(self, sqlite_service: SQLite3Service):
        """Test retrieving all devices for a user."""
        owner1 = DeviceOwner(
            serial="TEST12345678",
            user_id="user_123",
            created_at=datetime.utcnow(),
        )
        owner2 = DeviceOwner(
            serial="TEST87654321",
            user_id="user_123",
            created_at=datetime.utcnow(),
        )

        await sqlite_service.set_device_owner(owner1)
        await sqlite_service.set_device_owner(owner2)

        devices = await sqlite_service.get_user_devices("user_123")

        assert len(devices) == 2
        assert "TEST12345678" in devices
        assert "TEST87654321" in devices


class TestSQLite3ServiceWeather:
    """Tests for weather caching operations."""

    @pytest.mark.asyncio
    async def test_cache_and_get_weather(self, sqlite_service: SQLite3Service):
        """Test caching and retrieving weather data."""
        weather = WeatherData(
            postal_code="94102",
            country="US",
            fetched_at=datetime.utcnow(),
            data={"temperature": 18.5, "humidity": 65},
        )

        await sqlite_service.cache_weather(weather)
        retrieved = await sqlite_service.get_cached_weather("94102", "US")

        assert retrieved is not None
        assert retrieved.data["temperature"] == 18.5
