"""Tests for SQLModel service implementation."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from nolongerevil.lib.types import (
    APIKey,
    APIKeyPermissions,
    DeviceObject,
    DeviceOwner,
    DeviceShare,
    DeviceShareInvite,
    DeviceShareInviteStatus,
    DeviceSharePermission,
    EntryKey,
    IntegrationConfig,
    UserInfo,
    WeatherData,
)
from nolongerevil.services.sqlmodel_service import SQLModelService


class TestSQLModelService:
    """Tests for SQLModel storage backend."""

    async def test_device_object_crud(self, sqlmodel_service):
        """Test create, read, update, delete operations for device objects."""
        # Create
        obj = DeviceObject(
            serial="TEST123",
            object_key="device.TEST123",
            object_revision=1,
            object_timestamp=1234567890000,
            value={"test": "data", "number": 42},
            updated_at=datetime.now(),
        )
        await sqlmodel_service.upsert_object(obj)

        # Read
        retrieved = await sqlmodel_service.get_object("TEST123", "device.TEST123")
        assert retrieved is not None
        assert retrieved.serial == "TEST123"
        assert retrieved.object_key == "device.TEST123"
        assert retrieved.value["test"] == "data"
        assert retrieved.value["number"] == 42

        # Update
        obj.value["updated"] = True
        obj.object_revision = 2
        await sqlmodel_service.upsert_object(obj)

        updated = await sqlmodel_service.get_object("TEST123", "device.TEST123")
        assert updated is not None
        assert updated.value["updated"] is True
        assert updated.object_revision == 2

        # Delete
        deleted = await sqlmodel_service.delete_object("TEST123", "device.TEST123")
        assert deleted is True

        not_found = await sqlmodel_service.get_object("TEST123", "device.TEST123")
        assert not_found is None

    async def test_get_objects_by_serial(self, sqlmodel_service):
        """Test retrieving all objects for a device."""
        # Create multiple objects for same serial
        for i in range(3):
            obj = DeviceObject(
                serial="MULTI",
                object_key=f"key_{i}",
                object_revision=1,
                object_timestamp=1234567890000,
                value={"index": i},
                updated_at=datetime.now(),
            )
            await sqlmodel_service.upsert_object(obj)

        # Retrieve all
        objects = await sqlmodel_service.get_objects_by_serial("MULTI")
        assert len(objects) == 3
        assert all(obj.serial == "MULTI" for obj in objects)

    async def test_delete_device(self, sqlmodel_service):
        """Test deleting all objects for a device."""
        # Create multiple objects
        for i in range(5):
            obj = DeviceObject(
                serial="DELETE_ME",
                object_key=f"key_{i}",
                object_revision=1,
                object_timestamp=1234567890000,
                value={"index": i},
                updated_at=datetime.now(),
            )
            await sqlmodel_service.upsert_object(obj)

        # Delete all
        count = await sqlmodel_service.delete_device("DELETE_ME")
        assert count == 5

        # Verify all gone
        objects = await sqlmodel_service.get_objects_by_serial("DELETE_ME")
        assert len(objects) == 0

    async def test_user_operations(self, sqlmodel_service):
        """Test user creation and retrieval."""
        user = UserInfo(
            clerk_id="user_abc123",
            email="test@example.com",
            created_at=datetime.now(),
        )
        await sqlmodel_service.create_user(user)

        # Get by clerk_id
        retrieved = await sqlmodel_service.get_user("user_abc123")
        assert retrieved is not None
        assert retrieved.email == "test@example.com"

        # Get by email
        by_email = await sqlmodel_service.get_user_by_email("test@example.com")
        assert by_email is not None
        assert by_email.clerk_id == "user_abc123"

        # Update email
        user.email = "updated@example.com"
        await sqlmodel_service.create_user(user)

        updated = await sqlmodel_service.get_user("user_abc123")
        assert updated.email == "updated@example.com"

    async def test_entry_key_operations(self, sqlmodel_service):
        """Test entry key creation, retrieval, and claiming."""
        now = datetime.now()
        entry_key = EntryKey(
            code="ABC123",
            serial="DEVICE1",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        await sqlmodel_service.create_entry_key(entry_key)

        # Get by code
        retrieved = await sqlmodel_service.get_entry_key("ABC123")
        assert retrieved is not None
        assert retrieved.serial == "DEVICE1"
        assert retrieved.claimed_by is None

        # Get by serial
        by_serial = await sqlmodel_service.get_entry_key_by_serial("DEVICE1")
        assert by_serial is not None
        assert by_serial.code == "ABC123"

        # Claim
        claimed = await sqlmodel_service.claim_entry_key("ABC123", "user_xyz")
        assert claimed is True

        # Verify claimed
        after_claim = await sqlmodel_service.get_entry_key("ABC123")
        assert after_claim.claimed_by == "user_xyz"
        assert after_claim.claimed_at is not None

        # Can't claim again
        reclaim = await sqlmodel_service.claim_entry_key("ABC123", "user_other")
        assert reclaim is False

    async def test_device_ownership(self, sqlmodel_service):
        """Test device ownership operations."""
        owner = DeviceOwner(
            serial="OWNED1",
            user_id="user_owner",
            created_at=datetime.now(),
        )
        await sqlmodel_service.set_device_owner(owner)

        # Get owner
        retrieved = await sqlmodel_service.get_device_owner("OWNED1")
        assert retrieved is not None
        assert retrieved.user_id == "user_owner"

        # Get user devices
        devices = await sqlmodel_service.get_user_devices("user_owner")
        assert "OWNED1" in devices

    async def test_weather_caching(self, sqlmodel_service):
        """Test weather data caching."""
        weather = WeatherData(
            postal_code="12345",
            country="US",
            fetched_at=datetime.now(),
            data={
                "current": {"temp": 72, "condition": "sunny"},
                "location": {"city": "Test City"},
            },
        )
        await sqlmodel_service.cache_weather(weather)

        # Retrieve
        cached = await sqlmodel_service.get_cached_weather("12345", "US")
        assert cached is not None
        assert cached.data["current"]["temp"] == 72
        assert cached.data["location"]["city"] == "Test City"

        # Update
        weather.data["current"]["temp"] = 75
        await sqlmodel_service.cache_weather(weather)

        updated = await sqlmodel_service.get_cached_weather("12345", "US")
        assert updated.data["current"]["temp"] == 75

    async def test_sync_user_weather_extracts_keyed_location(self):
        """Test that user weather is extracted from the Nest location key."""
        serial = "WEATHER1"
        user_id = "weather_user"
        now = datetime.now()
        service = SQLModelService.__new__(SQLModelService)
        user_state = DeviceObject(serial, f"user.{user_id}", 1, 1, {}, now)
        service.get_user_devices = AsyncMock(return_value=[serial])
        service.get_object = AsyncMock(
            side_effect=[
                DeviceObject(
                    serial, f"device.{serial}", 1, 1,
                    {"postal_code": "39440", "country": "US"}, now
                ),
                user_state,
            ]
        )
        service.get_cached_weather = AsyncMock(
            return_value=WeatherData(
                "39440", "US", now,
                {"39440,US": {"current": {"temp_c": 36.1}, "location": {"city": "Test"}}},
            )
        )
        service.upsert_object = AsyncMock()

        await service.sync_user_weather_from_device(user_id)

        updated_state = service.upsert_object.await_args.args[0]
        assert updated_state.value["weather"]["current"]["temp_c"] == 36.1
        assert updated_state.value["weather"]["location"]["city"] == "Test"

    @pytest.mark.parametrize(
        "weather_data",
        [
            {"other-location": {}},
            {"A1B 2C3,CA": "not-an-object"},
            {"A1B 2C3,CA": {}},
            {"A1B 2C3,CA": {"current": None}},
        ],
    )
    async def test_sync_user_weather_ignores_missing_or_malformed_location(self, weather_data):
        """Test that invalid keyed weather data does not overwrite user weather."""
        serial = "WEATHER2"
        user_id = "weather_user_2"
        now = datetime.now()
        existing_weather = {"current": {"temp_c": 20}}
        service = SQLModelService.__new__(SQLModelService)
        service.get_user_devices = AsyncMock(return_value=[serial])
        service.get_object = AsyncMock(
            side_effect=[
                DeviceObject(
                    serial, f"device.{serial}", 1, 1,
                    {"postal_code": "A1B 2C3", "country": "CA"}, now
                ),
                DeviceObject(
                    serial, f"user.{user_id}", 1, 1,
                    {"weather": existing_weather}, now
                ),
            ]
        )
        service.get_cached_weather = AsyncMock(
            return_value=WeatherData("A1B 2C3", "CA", now, weather_data)
        )
        service.upsert_object = AsyncMock()

        await service.sync_user_weather_from_device(user_id)

        service.upsert_object.assert_not_awaited()

    async def test_api_key_operations(self, sqlmodel_service):
        """Test API key CRUD operations."""
        api_key = APIKey(
            id="1",
            key_hash="hash123",
            key_preview="sk_test_...",
            user_id="user_123",
            name="Test Key",
            permissions=APIKeyPermissions(
                devices=["DEVICE1", "DEVICE2"],
                scopes=["read", "write"],
            ),
            created_at=datetime.now(),
        )
        await sqlmodel_service.create_api_key(api_key)

        # Get by hash
        retrieved = await sqlmodel_service.get_api_key_by_hash("hash123")
        assert retrieved is not None
        assert retrieved.name == "Test Key"
        assert retrieved.permissions.devices == ["DEVICE1", "DEVICE2"]
        assert "read" in retrieved.permissions.scopes

        # Get user keys
        user_keys = await sqlmodel_service.get_user_api_keys("user_123")
        assert len(user_keys) >= 1
        assert any(k.key_hash == "hash123" for k in user_keys)

        # Update last used
        await sqlmodel_service.update_api_key_last_used(retrieved.id)
        after_use = await sqlmodel_service.get_api_key_by_hash("hash123")
        assert after_use.last_used_at is not None

        # Delete
        deleted = await sqlmodel_service.delete_api_key(retrieved.id)
        assert deleted is True

        not_found = await sqlmodel_service.get_api_key_by_hash("hash123")
        assert not_found is None

    async def test_device_sharing(self, sqlmodel_service):
        """Test device sharing operations."""
        share = DeviceShare(
            owner_id="owner1",
            shared_with_user_id="user2",
            serial="SHARED1",
            permissions=DeviceSharePermission.READ,
            created_at=datetime.now(),
        )
        await sqlmodel_service.create_device_share(share)

        # Get device shares
        shares = await sqlmodel_service.get_device_shares("SHARED1")
        assert len(shares) == 1
        assert shares[0].shared_with_user_id == "user2"
        assert shares[0].permissions == DeviceSharePermission.READ

        # Get user shared devices
        user_shares = await sqlmodel_service.get_user_shared_devices("user2")
        assert len(user_shares) == 1
        assert user_shares[0].serial == "SHARED1"

        # Update permissions
        share.permissions = DeviceSharePermission.CONTROL
        await sqlmodel_service.create_device_share(share)

        updated_shares = await sqlmodel_service.get_device_shares("SHARED1")
        assert updated_shares[0].permissions == DeviceSharePermission.CONTROL

        # Delete share
        deleted = await sqlmodel_service.delete_device_share("owner1", "user2", "SHARED1")
        assert deleted is True

        no_shares = await sqlmodel_service.get_device_shares("SHARED1")
        assert len(no_shares) == 0

    async def test_device_share_invites(self, sqlmodel_service):
        """Test device share invitation operations."""
        invite = DeviceShareInvite(
            invite_token="token123",
            owner_id="owner1",
            email="invitee@example.com",
            serial="DEVICE1",
            permissions=DeviceSharePermission.WRITE,
            status=DeviceShareInviteStatus.PENDING,
            invited_at=datetime.now(),
            expires_at=datetime.now() + timedelta(days=7),
        )
        await sqlmodel_service.create_device_share_invite(invite)

        # Get invite
        retrieved = await sqlmodel_service.get_device_share_invite("token123")
        assert retrieved is not None
        assert retrieved.email == "invitee@example.com"
        assert retrieved.status == DeviceShareInviteStatus.PENDING

        # Accept invite
        accepted = await sqlmodel_service.accept_device_share_invite("token123", "user_invitee")
        assert accepted is True

        after_accept = await sqlmodel_service.get_device_share_invite("token123")
        assert after_accept.status == DeviceShareInviteStatus.ACCEPTED
        assert after_accept.shared_with_user_id == "user_invitee"

    async def test_integration_operations(self, sqlmodel_service):
        """Test integration configuration operations."""
        integration = IntegrationConfig(
            user_id="user1",
            type="mqtt",
            enabled=True,
            config={"broker": "mqtt://localhost", "topic": "test"},
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        await sqlmodel_service.upsert_integration(integration)

        # Get user integrations
        integrations = await sqlmodel_service.get_integrations("user1")
        assert len(integrations) == 1
        assert integrations[0].type == "mqtt"
        assert integrations[0].config["broker"] == "mqtt://localhost"

        # Get enabled integrations
        enabled = await sqlmodel_service.get_enabled_integrations()
        assert len(enabled) >= 1
        assert any(i.type == "mqtt" for i in enabled)

        # Update
        integration.config["topic"] = "updated"
        integration.updated_at = datetime.now()
        await sqlmodel_service.upsert_integration(integration)

        updated = await sqlmodel_service.get_integrations("user1")
        assert updated[0].config["topic"] == "updated"

        # Delete
        deleted = await sqlmodel_service.delete_integration("user1", "mqtt")
        assert deleted is True

        no_integrations = await sqlmodel_service.get_integrations("user1")
        assert len(no_integrations) == 0

    async def test_generate_entry_key(self, sqlmodel_service):
        """Test entry key generation."""
        result = await sqlmodel_service.generate_entry_key("DEVICE1", ttl_seconds=3600)

        assert result is not None
        assert "code" in result
        assert "expiresAt" in result
        assert len(result["code"]) == 7  # 3 digits + 4 letters

        # Verify it was stored
        entry_key = await sqlmodel_service.get_entry_key(result["code"])
        assert entry_key is not None
        assert entry_key.serial == "DEVICE1"

    async def test_validate_api_key(self, sqlmodel_service):
        """Test API key validation."""
        # Create user first
        user = UserInfo(
            clerk_id="user_test",
            email="test@example.com",
            created_at=datetime.now(),
        )
        await sqlmodel_service.create_user(user)

        # Create API key
        from nolongerevil.services.sqlmodel_service import hash_api_key

        raw_key = "test_key_12345"
        api_key = APIKey(
            id="1",
            key_hash=hash_api_key(raw_key),
            key_preview="test_...",
            user_id="user_test",
            name="Test",
            permissions=APIKeyPermissions(devices=[], scopes=["read", "write"]),
            created_at=datetime.now(),
        )
        await sqlmodel_service.create_api_key(api_key)

        # Validate
        result = await sqlmodel_service.validate_api_key(raw_key)
        assert result is not None
        assert result["userId"] == "user_test"
        assert "read" in result["permissions"]["scopes"]

    async def test_list_user_devices(self, sqlmodel_service):
        """Test listing user devices."""
        # Create user
        user = UserInfo(clerk_id="user_list", email="list@example.com", created_at=datetime.now())
        await sqlmodel_service.create_user(user)

        # Create ownership
        for i in range(3):
            owner = DeviceOwner(
                serial=f"DEVICE{i}",
                user_id="user_list",
                created_at=datetime.now(),
            )
            await sqlmodel_service.set_device_owner(owner)

        # List
        devices = await sqlmodel_service.list_user_devices("user_list")
        assert len(devices) == 3
        assert all("serial" in d for d in devices)


@pytest.mark.asyncio
async def test_sqlmodel_specific_initialization(sqlmodel_service):
    """Test SQLModel-specific initialization."""
    # Verify service is initialized
    assert sqlmodel_service.engine is not None
    assert sqlmodel_service._session_maker is not None

    # Can perform basic operations
    obj = DeviceObject(
        serial="INIT_TEST",
        object_key="key",
        object_revision=1,
        object_timestamp=123,
        value={},
        updated_at=datetime.now(),
    )
    await sqlmodel_service.upsert_object(obj)

    retrieved = await sqlmodel_service.get_object("INIT_TEST", "key")
    assert retrieved is not None
