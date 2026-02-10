"""SQLModel implementation of device state persistence."""

import hashlib
import json
import random
import string
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel, select

from nolongerevil.config import settings
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import (
    APIKey,
    DeviceObject,
    DeviceOwner,
    DeviceShare,
    DeviceShareInvite,
    DeviceSharePermission,
    EntryKey,
    IntegrationConfig,
    UserInfo,
    WeatherData,
)
from nolongerevil.models import (
    APIKeyModel,
    DeviceObjectModel,
    DeviceOwnerModel,
    DeviceShareInviteModel,
    DeviceShareModel,
    EntryKeyModel,
    IntegrationConfigModel,
    LogModel,
    SessionModel,
    UserInfoModel,
    WeatherDataModel,
)
from nolongerevil.models.base import ms_to_timestamp, now_ms, timestamp_to_ms
from nolongerevil.models.converters import (
    api_key_to_model,
    device_object_to_model,
    device_owner_to_model,
    device_share_invite_to_model,
    device_share_to_model,
    entry_key_to_model,
    integration_config_to_model,
    model_to_api_key,
    model_to_device_object,
    model_to_device_owner,
    model_to_device_share,
    model_to_device_share_invite,
    model_to_entry_key,
    model_to_integration_config,
    model_to_user_info,
    model_to_weather_data,
    user_info_to_model,
    weather_data_to_model,
)
from nolongerevil.services.abstract_device_state_manager import AbstractDeviceStateManager

logger = get_logger(__name__)


class SQLModelService(AbstractDeviceStateManager):
    """SQLModel implementation of device state persistence."""

    def __init__(self, db_url: str | None = None) -> None:
        """Initialize the SQLModel service.

        Args:
            db_url: Database URL. Defaults to SQLite path from settings.
        """
        if db_url:
            self.db_url = db_url
        else:
            # Ensure directory exists
            Path(settings.sqlite3_db_path).parent.mkdir(parents=True, exist_ok=True)
            self.db_url = f"sqlite+aiosqlite:///{settings.sqlite3_db_path}"

        self.engine: AsyncEngine | None = None
        self.__session_maker: async_sessionmaker[AsyncSession] | None = None

    @property
    def _session_maker(self) -> async_sessionmaker[AsyncSession]:
        """Get the session maker, raising if not initialized."""
        if self.__session_maker is None:
            raise RuntimeError("SQLModelService not initialized. Call initialize() first.")
        return self.__session_maker

    async def initialize(self) -> None:
        """Initialize the database connection and schema."""
        self.engine = create_async_engine(
            self.db_url,
            echo=False,
            future=True,
        )

        self.__session_maker = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # Create tables
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        logger.info(f"SQLModel database initialized at {self.db_url}")

    async def close(self) -> None:
        """Close the database connection."""
        if self.engine:
            await self.engine.dispose()
            self.engine = None
            self.__session_maker = None
            logger.info("SQLModel database connection closed")

    # Device state operations

    async def get_object(self, serial: str, object_key: str) -> DeviceObject | None:
        """Get a single device object by serial and key."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(DeviceObjectModel).where(
                    DeviceObjectModel.serial == serial,
                    DeviceObjectModel.object_key == object_key,
                )
            )
            model = result.scalar_one_or_none()
            return model_to_device_object(model) if model else None

    async def get_objects_by_serial(self, serial: str) -> list[DeviceObject]:
        """Get all objects for a device."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(DeviceObjectModel).where(DeviceObjectModel.serial == serial)
            )
            models = result.scalars().all()
            return [model_to_device_object(model) for model in models]

    async def get_all_objects(self) -> list[DeviceObject]:
        """Get all device objects."""
        async with self._session_maker() as session:
            result = await session.execute(select(DeviceObjectModel))
            models = result.scalars().all()
            return [model_to_device_object(model) for model in models]

    async def upsert_object(self, obj: DeviceObject) -> None:
        """Insert or update a device object."""
        async with self._session_maker() as session:
            # Try to find existing
            result = await session.execute(
                select(DeviceObjectModel).where(
                    DeviceObjectModel.serial == obj.serial,
                    DeviceObjectModel.object_key == obj.object_key,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Update existing
                existing.object_revision = obj.object_revision
                existing.object_timestamp = obj.object_timestamp
                existing.value = json.dumps(obj.value)
                existing.updatedAt = now_ms()
            else:
                # Insert new
                model = device_object_to_model(obj)
                session.add(model)

            await session.commit()

    async def delete_object(self, serial: str, object_key: str) -> bool:
        """Delete a device object."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(DeviceObjectModel).where(
                    DeviceObjectModel.serial == serial,
                    DeviceObjectModel.object_key == object_key,
                )
            )
            model = result.scalar_one_or_none()

            if model:
                await session.delete(model)
                await session.commit()
                return True
            return False

    async def delete_device(self, serial: str) -> int:
        """Delete all objects for a device."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(DeviceObjectModel).where(DeviceObjectModel.serial == serial)
            )
            models = result.scalars().all()
            count = len(models)

            for model in models:
                await session.delete(model)

            await session.commit()
            return count

    # Entry key operations

    async def create_entry_key(self, entry_key: EntryKey) -> None:
        """Create a new entry key for device pairing."""
        async with self._session_maker() as session:
            model = entry_key_to_model(entry_key)
            session.add(model)
            await session.commit()

    async def get_entry_key(self, code: str) -> EntryKey | None:
        """Get an entry key by code."""
        async with self._session_maker() as session:
            result = await session.execute(select(EntryKeyModel).where(EntryKeyModel.code == code))
            model = result.scalar_one_or_none()
            return model_to_entry_key(model) if model else None

    async def get_entry_key_by_serial(self, serial: str) -> EntryKey | None:
        """Get an unexpired entry key by serial."""
        now = now_ms()
        async with self._session_maker() as session:
            result = await session.execute(
                select(EntryKeyModel)
                .where(
                    EntryKeyModel.serial == serial,
                    EntryKeyModel.expiresAt > now,
                    EntryKeyModel.claimedBy.is_(None),
                )
                .order_by(EntryKeyModel.createdAt.desc())
                .limit(1)
            )
            model = result.scalar_one_or_none()
            return model_to_entry_key(model) if model else None

    async def get_latest_entry_key_by_serial(self, serial: str) -> EntryKey | None:
        """Get the most recent entry key for a serial (including claimed or expired keys).

        This is used for checking pairing status.
        """
        async with self._session_maker() as session:
            result = await session.execute(
                select(EntryKeyModel)
                .where(EntryKeyModel.serial == serial)
                .order_by(EntryKeyModel.createdAt.desc())
                .limit(1)
            )
            model = result.scalar_one_or_none()
            return model_to_entry_key(model) if model else None

    async def claim_entry_key(self, code: str, user_id: str) -> bool:
        """Claim an entry key for a user."""
        now = now_ms()
        async with self._session_maker() as session:
            result = await session.execute(
                select(EntryKeyModel).where(
                    EntryKeyModel.code == code,
                    EntryKeyModel.claimedBy.is_(None),
                    EntryKeyModel.expiresAt > now,
                )
            )
            model = result.scalar_one_or_none()

            if model:
                model.claimedBy = user_id
                model.claimedAt = now
                await session.commit()
                return True
            return False

    # User operations

    async def create_user(self, user: UserInfo) -> None:
        """Create a new user."""
        async with self._session_maker() as session:
            # Check if user exists
            result = await session.execute(
                select(UserInfoModel).where(UserInfoModel.clerkId == user.clerk_id)
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Update email
                existing.email = user.email
            else:
                # Insert new
                model = user_info_to_model(user)
                session.add(model)

            await session.commit()

    async def get_user(self, clerk_id: str) -> UserInfo | None:
        """Get a user by clerk ID."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(UserInfoModel).where(UserInfoModel.clerkId == clerk_id)
            )
            model = result.scalar_one_or_none()
            return model_to_user_info(model) if model else None

    async def get_user_by_email(self, email: str) -> UserInfo | None:
        """Get a user by email."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(UserInfoModel).where(UserInfoModel.email == email)
            )
            model = result.scalar_one_or_none()
            return model_to_user_info(model) if model else None

    # Device owner operations

    async def set_device_owner(self, owner: DeviceOwner) -> None:
        """Set the owner of a device."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(DeviceOwnerModel).where(DeviceOwnerModel.serial == owner.serial)
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.userId = owner.user_id
                existing.createdAt = timestamp_to_ms(owner.created_at) or now_ms()
            else:
                model = device_owner_to_model(owner)
                session.add(model)

            await session.commit()

    async def get_device_owner(self, serial: str) -> DeviceOwner | None:
        """Get the owner of a device."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(DeviceOwnerModel).where(DeviceOwnerModel.serial == serial)
            )
            model = result.scalar_one_or_none()
            return model_to_device_owner(model) if model else None

    async def get_user_devices(self, user_id: str) -> list[str]:
        """Get all device serials owned by a user."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(DeviceOwnerModel).where(DeviceOwnerModel.userId == user_id)
            )
            models = result.scalars().all()
            return [model.serial for model in models]

    async def get_all_registered_serials(self) -> list[str]:
        """Get all device serials that have an ownership record."""
        async with self._session_maker() as session:
            result = await session.execute(select(DeviceOwnerModel))
            models = result.scalars().all()
            return [model.serial for model in models]

    async def delete_device_owner(self, serial: str, user_id: str) -> bool:
        """Delete device ownership record."""
        async with self._session_maker() as session:
            result = await session.execute(
                delete(DeviceOwnerModel).where(
                    DeviceOwnerModel.serial == serial,
                    DeviceOwnerModel.userId == user_id,
                )
            )
            await session.commit()
            return result.rowcount > 0

    # Weather operations

    async def get_cached_weather(self, postal_code: str, country: str) -> WeatherData | None:
        """Get cached weather data."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(WeatherDataModel)
                .where(
                    WeatherDataModel.postalCode == postal_code,
                    WeatherDataModel.country == country,
                )
                .order_by(WeatherDataModel.fetchedAt.desc())  # Most recent first
                .limit(1)
            )
            model = result.scalar_one_or_none()
            return model_to_weather_data(model) if model else None

    async def cache_weather(self, weather: WeatherData) -> None:
        """Cache weather data.

        Note: Deletes all existing entries and inserts a new one to avoid duplicates.
        """
        from sqlalchemy import delete

        async with self._session_maker() as session:
            # Delete all existing entries for this postal_code/country
            await session.execute(
                delete(WeatherDataModel).where(
                    WeatherDataModel.postalCode == weather.postal_code,
                    WeatherDataModel.country == weather.country,
                )
            )

            # Insert new entry
            model = weather_data_to_model(weather)
            session.add(model)

            await session.commit()

    # API key operations

    async def create_api_key(self, api_key: APIKey) -> None:
        """Create a new API key."""
        async with self._session_maker() as session:
            model = api_key_to_model(api_key)
            session.add(model)
            await session.commit()

    async def get_api_key_by_hash(self, key_hash: str) -> APIKey | None:
        """Get an API key by its hash."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(APIKeyModel).where(APIKeyModel.keyHash == key_hash)
            )
            model = result.scalar_one_or_none()
            return model_to_api_key(model) if model else None

    async def update_api_key_last_used(self, key_id: str) -> None:
        """Update the last used timestamp of an API key."""
        async with self._session_maker() as session:
            result = await session.execute(select(APIKeyModel).where(APIKeyModel.id == int(key_id)))
            model = result.scalar_one_or_none()

            if model:
                model.lastUsedAt = now_ms()
                await session.commit()

    async def delete_api_key(self, key_id: str) -> bool:
        """Delete an API key."""
        async with self._session_maker() as session:
            result = await session.execute(select(APIKeyModel).where(APIKeyModel.id == int(key_id)))
            model = result.scalar_one_or_none()

            if model:
                await session.delete(model)
                await session.commit()
                return True
            return False

    async def get_user_api_keys(self, user_id: str) -> list[APIKey]:
        """Get all API keys for a user."""
        async with self._session_maker() as session:
            result = await session.execute(select(APIKeyModel).where(APIKeyModel.userId == user_id))
            models = result.scalars().all()
            return [model_to_api_key(model) for model in models]

    # Device sharing operations

    async def create_device_share(self, share: DeviceShare) -> None:
        """Create a device share."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(DeviceShareModel).where(
                    DeviceShareModel.ownerId == share.owner_id,
                    DeviceShareModel.sharedWithUserId == share.shared_with_user_id,
                    DeviceShareModel.serial == share.serial,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.permissions = share.permissions.value
            else:
                model = device_share_to_model(share)
                session.add(model)

            await session.commit()

    async def get_device_shares(self, serial: str) -> list[DeviceShare]:
        """Get all shares for a device."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(DeviceShareModel).where(DeviceShareModel.serial == serial)
            )
            models = result.scalars().all()
            return [model_to_device_share(model) for model in models]

    async def get_user_shared_devices(self, user_id: str) -> list[DeviceShare]:
        """Get all devices shared with a user."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(DeviceShareModel).where(DeviceShareModel.sharedWithUserId == user_id)
            )
            models = result.scalars().all()
            return [model_to_device_share(model) for model in models]

    async def delete_device_share(
        self, owner_id: str, shared_with_user_id: str, serial: str
    ) -> bool:
        """Delete a device share."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(DeviceShareModel).where(
                    DeviceShareModel.ownerId == owner_id,
                    DeviceShareModel.sharedWithUserId == shared_with_user_id,
                    DeviceShareModel.serial == serial,
                )
            )
            model = result.scalar_one_or_none()

            if model:
                await session.delete(model)
                await session.commit()
                return True
            return False

    # Device share invite operations

    async def create_device_share_invite(self, invite: DeviceShareInvite) -> None:
        """Create a device share invitation."""
        async with self._session_maker() as session:
            model = device_share_invite_to_model(invite)
            session.add(model)
            await session.commit()

    async def get_device_share_invite(self, invite_token: str) -> DeviceShareInvite | None:
        """Get an invitation by token."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(DeviceShareInviteModel).where(
                    DeviceShareInviteModel.inviteToken == invite_token
                )
            )
            model = result.scalar_one_or_none()
            return model_to_device_share_invite(model) if model else None

    async def accept_device_share_invite(self, invite_token: str, user_id: str) -> bool:
        """Accept a device share invitation."""
        now = now_ms()
        async with self._session_maker() as session:
            result = await session.execute(
                select(DeviceShareInviteModel).where(
                    DeviceShareInviteModel.inviteToken == invite_token,
                    DeviceShareInviteModel.status == "pending",
                    DeviceShareInviteModel.expiresAt > now,
                )
            )
            model = result.scalar_one_or_none()

            if model:
                model.status = "accepted"
                model.acceptedAt = now
                model.sharedWithUserId = user_id
                await session.commit()
                return True
            return False

    # Integration operations

    async def get_integrations(self, user_id: str) -> list[IntegrationConfig]:
        """Get all integrations for a user."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(IntegrationConfigModel).where(IntegrationConfigModel.userId == user_id)
            )
            models = result.scalars().all()
            return [model_to_integration_config(model) for model in models]

    async def get_enabled_integrations(self) -> list[IntegrationConfig]:
        """Get all enabled integrations."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(IntegrationConfigModel).where(IntegrationConfigModel.enabled == 1)
            )
            models = result.scalars().all()
            return [model_to_integration_config(model) for model in models]

    async def upsert_integration(self, integration: IntegrationConfig) -> None:
        """Create or update an integration."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(IntegrationConfigModel).where(
                    IntegrationConfigModel.userId == integration.user_id,
                    IntegrationConfigModel.type == integration.type,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.enabled = 1 if integration.enabled else 0
                existing.config = json.dumps(integration.config)
                existing.updatedAt = timestamp_to_ms(integration.updated_at) or now_ms()
            else:
                model = integration_config_to_model(integration)
                session.add(model)

            await session.commit()

    async def delete_integration(self, user_id: str, integration_type: str) -> bool:
        """Delete an integration."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(IntegrationConfigModel).where(
                    IntegrationConfigModel.userId == user_id,
                    IntegrationConfigModel.type == integration_type,
                )
            )
            model = result.scalar_one_or_none()

            if model:
                await session.delete(model)
                await session.commit()
                return True
            return False

    # Session logging

    async def log_session(
        self,
        serial: str,
        session_id: str,
        endpoint: str,
        client: str | None,
        meta: dict[str, Any] | None,
    ) -> None:
        """Log a device session."""
        now = now_ms()
        async with self._session_maker() as session_obj:
            result = await session_obj.execute(
                select(SessionModel).where(
                    SessionModel.serial == serial,
                    SessionModel.session == session_id,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.lastActivity = now
                existing.open = 1
            else:
                model = SessionModel(
                    serial=serial,
                    session=session_id,
                    endpoint=endpoint,
                    startedAt=now,
                    lastActivity=now,
                    open=1,
                    client=client,
                    meta=json.dumps(meta) if meta else None,
                )
                session_obj.add(model)

            await session_obj.commit()

    async def update_session_activity(self, serial: str, session_id: str) -> None:
        """Update the last activity timestamp for a session."""
        async with self._session_maker() as session_obj:
            result = await session_obj.execute(
                select(SessionModel).where(
                    SessionModel.serial == serial,
                    SessionModel.session == session_id,
                )
            )
            model = result.scalar_one_or_none()

            if model:
                model.lastActivity = now_ms()
                await session_obj.commit()

    async def close_session(self, serial: str, session_id: str) -> None:
        """Mark a session as closed."""
        async with self._session_maker() as session_obj:
            result = await session_obj.execute(
                select(SessionModel).where(
                    SessionModel.serial == serial,
                    SessionModel.session == session_id,
                )
            )
            model = result.scalar_one_or_none()

            if model:
                model.open = 0
                await session_obj.commit()

    # Request logging

    async def log_request(
        self,
        route: str,
        serial: str | None,
        request_data: dict[str, Any],
        response_data: dict[str, Any],
    ) -> None:
        """Log a request/response pair."""
        async with self._session_maker() as session:
            model = LogModel(
                ts=now_ms(),
                route=route,
                serial=serial,
                req=json.dumps(request_data),
                res=json.dumps(response_data),
            )
            session.add(model)
            await session.commit()

    # Additional methods from TypeScript AbstractDeviceStateManager

    async def generate_entry_key(
        self, serial: str, ttl_seconds: int = 3600
    ) -> dict[str, Any] | None:
        """Generate entry key for device pairing."""
        try:
            now = now_ms()
            expires_at = now + (ttl_seconds * 1000)

            async with self._session_maker() as session:
                # Delete all existing entry keys for this serial
                result = await session.execute(
                    select(EntryKeyModel).where(EntryKeyModel.serial == serial)
                )
                existing_keys = result.scalars().all()
                for key in existing_keys:
                    await session.delete(key)
                await session.commit()

                # Generate unique code
                code = None
                for _ in range(20):
                    digits = "".join(random.choices(string.digits, k=3))
                    letters = "".join(random.choices(string.ascii_uppercase, k=4))
                    candidate = f"{digits}{letters}"

                    # Check if code already exists
                    result = await session.execute(
                        select(EntryKeyModel).where(EntryKeyModel.code == candidate)
                    )
                    if not result.scalar_one_or_none():
                        code = candidate
                        break

                if not code:
                    logger.error(f"Unable to allocate entry key for {serial}")
                    return None

                # Create entry key
                model = EntryKeyModel(
                    code=code,
                    serial=serial,
                    createdAt=now,
                    expiresAt=expires_at,
                )
                session.add(model)
                await session.commit()

                return {"code": code, "expiresAt": expires_at}

        except Exception as e:
            logger.error(f"Failed to generate entry key for {serial}: {e}")
            return None

    async def update_user_away_status(self, user_id: str) -> None:
        """Update user away status based on device state."""
        try:
            user_id = user_id.replace("user_", "")
            devices = await self.get_user_devices(user_id)

            if not devices:
                return

            all_away = True
            any_reported = False
            most_recent_timestamp = 0
            most_recent_setter = None
            has_vacation = False

            for serial in devices:
                device_obj = await self.get_object(serial, f"device.{serial}")
                if device_obj and device_obj.value:
                    any_reported = True
                    away = bool(device_obj.value.get("away"))
                    away_ts = device_obj.value.get("away_timestamp", 0) or 0
                    away_setter = device_obj.value.get("away_setter")
                    vacation = device_obj.value.get("vacation_mode", False)

                    if vacation:
                        has_vacation = True

                    if away_ts > most_recent_timestamp:
                        most_recent_timestamp = away_ts
                        most_recent_setter = away_setter

                    if not away:
                        all_away = False
                        break

            user_away = all_away if any_reported else False

            # Update user state on each device
            now = now_ms()
            for serial in devices:
                user_key = f"user.{user_id}"
                user_state = await self.get_object(serial, user_key)

                if user_state:
                    updated_value = {
                        **(user_state.value or {}),
                        "away": user_away,
                        "vacation_mode": has_vacation,
                    }
                    if most_recent_timestamp > 0:
                        updated_value["away_timestamp"] = most_recent_timestamp
                    if most_recent_setter:
                        updated_value["away_setter"] = most_recent_setter

                    await self.upsert_object(
                        DeviceObject(
                            serial=serial,
                            object_key=user_key,
                            object_revision=(user_state.object_revision or 0) + 1,
                            object_timestamp=now,
                            value=updated_value,
                            updated_at=ms_to_timestamp(now) or datetime.now(),
                        )
                    )

        except Exception as e:
            logger.error(f"Failed to update away status for {user_id}: {e}")

    async def sync_user_weather_from_device(self, user_id: str) -> None:
        """Sync user weather from device postal code."""
        try:
            user_id = user_id.replace("user_", "")
            devices = await self.get_user_devices(user_id)

            if not devices:
                return

            postal_code = None
            country = "US"

            for serial in devices:
                device_obj = await self.get_object(serial, f"device.{serial}")
                if device_obj and device_obj.value:
                    pc = device_obj.value.get("postal_code")
                    if pc:
                        postal_code = pc
                        country = device_obj.value.get("country", "US")
                        break

            if not postal_code:
                return

            weather = await self.get_cached_weather(postal_code, country)
            if not weather:
                return

            now = now_ms()
            weather_data = {
                "current": weather.data.get("current"),
                "location": weather.data.get("location"),
                "updatedAt": now,
            }

            for serial in devices:
                user_key = f"user.{user_id}"
                user_state = await self.get_object(serial, user_key)
                if user_state:
                    updated_value = {**(user_state.value or {}), "weather": weather_data}
                    await self.upsert_object(
                        DeviceObject(
                            serial=serial,
                            object_key=user_key,
                            object_revision=(user_state.object_revision or 0) + 1,
                            object_timestamp=now,
                            value=updated_value,
                            updated_at=ms_to_timestamp(now) or datetime.now(),
                        )
                    )

        except Exception as e:
            logger.error(f"Failed to sync weather for {user_id}: {e}")

    async def ensure_device_alert_dialog(self, serial: str) -> None:
        """Ensure device alert dialog exists."""
        try:
            now = now_ms()
            device_owner = await self.get_device_owner(serial)

            if not device_owner:
                return

            user_info = await self.get_user(device_owner.user_id)
            user_email = user_info.email if user_info else ""
            user_id = device_owner.user_id.replace("user_", "")
            user_state_key = f"user.{user_id}"
            structure_id = f"structure.{user_id}"

            # Ensure alert dialog exists
            alert_dialog_key = f"device_alert_dialog.{serial}"
            existing_dialog = await self.get_object(serial, alert_dialog_key)
            if not existing_dialog:
                dialog_value = {"dialog_data": "", "dialog_id": "confirm-pairing"}
                await self.upsert_object(
                    DeviceObject(
                        serial=serial,
                        object_key=alert_dialog_key,
                        object_revision=1,
                        object_timestamp=now,
                        value=dialog_value,
                        updated_at=ms_to_timestamp(now) or datetime.now(),
                    )
                )

            # Ensure user state exists
            existing_user_state = await self.get_object(serial, user_state_key)
            if not existing_user_state:
                default_user_state = {
                    "acknowledged_onboarding_screens": ["rcs"],
                    "email": user_email,
                    "name": "",
                    "obsidian_version": "5.58rc3",
                    "profile_image_url": "",
                    "short_name": "",
                    "structures": [structure_id],
                    "structure_memberships": [{"structure": structure_id, "roles": ["owner"]}],
                }
                await self.upsert_object(
                    DeviceObject(
                        serial=serial,
                        object_key=user_state_key,
                        object_revision=1,
                        object_timestamp=now,
                        value=default_user_state,
                        updated_at=ms_to_timestamp(now) or datetime.now(),
                    )
                )

        except Exception as e:
            logger.error(f"Failed to ensure alert dialog for {serial}: {e}")

    async def get_user_weather(self, user_id: str) -> dict[str, Any] | None:
        """Get user's weather data."""
        try:
            user_id = user_id.replace("user_", "")
            devices = await self.get_user_devices(user_id)

            if not devices:
                return None

            user_key = f"user.{user_id}"
            user_state = await self.get_object(devices[0], user_key)

            if user_state and user_state.value:
                return user_state.value.get("weather")
            return None

        except Exception as e:
            logger.error(f"Failed to get user weather for {user_id}: {e}")
            return None

    async def get_all_enabled_mqtt_integrations(self) -> list[dict[str, Any]]:
        """Get all enabled MQTT integrations for loading by IntegrationManager."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(IntegrationConfigModel).where(
                    IntegrationConfigModel.type == "mqtt",
                    IntegrationConfigModel.enabled == 1,
                )
            )
            models = result.scalars().all()
            return [
                {"userId": model.userId, "config": json.loads(model.config or "{}")}
                for model in models
            ]

    async def validate_api_key(self, key: str) -> dict[str, Any] | None:
        """Validate API key for authentication."""
        try:
            key_hash = hash_api_key(key)
            api_key = await self.get_api_key_by_hash(key_hash)

            if not api_key:
                return None

            # Check if expired
            if api_key.expires_at and api_key.expires_at < datetime.now():
                return None

            # Update last used
            await self.update_api_key_last_used(api_key.id)

            return {
                "userId": api_key.user_id,
                "permissions": {
                    "devices": api_key.permissions.devices,
                    "scopes": api_key.permissions.scopes,
                },
                "keyId": api_key.id,
            }

        except Exception as e:
            logger.error(f"Failed to validate API key: {e}")
            return None

    async def check_api_key_permission(
        self,
        user_id: str,
        serial: str,
        required_scopes: list[str],
        permissions: dict[str, Any],
    ) -> bool:
        """Check if API key has permission to access a device."""
        try:
            serials = permissions.get("serials", []) or permissions.get("devices", [])
            scopes = permissions.get("scopes", [])

            # Check if device is in allowed serials list
            if serials and serial not in serials:
                return False

            # Check if user owns the device
            owner = await self.get_device_owner(serial)
            if owner and owner.user_id == user_id:
                return all(scope in scopes for scope in required_scopes)

            # Check if device is shared with user
            shared_devices = await self.get_user_shared_devices(user_id)
            for share in shared_devices:
                if share.serial == serial:
                    has_share_perms = all(
                        scope == "read"
                        or (
                            scope in ("write", "control")
                            and share.permissions == DeviceSharePermission.CONTROL
                        )
                        for scope in required_scopes
                    )
                    has_key_scope = all(scope in scopes for scope in required_scopes)
                    return has_share_perms and has_key_scope

            return False

        except Exception as e:
            logger.error(f"Failed to check API key permission: {e}")
            return False

    async def list_user_devices(self, user_id: str) -> list[dict[str, str]]:
        """List all devices owned by a user."""
        serials = await self.get_user_devices(user_id)
        return [{"serial": s} for s in serials]

    async def get_shared_with_me(self, user_id: str) -> list[dict[str, Any]]:
        """Get devices shared with a user."""
        shares = await self.get_user_shared_devices(user_id)
        return [{"serial": s.serial, "permissions": [s.permissions.value]} for s in shares]


def hash_api_key(key: str) -> str:
    """Hash an API key for storage.

    Args:
        key: Raw API key

    Returns:
        SHA-256 hash of the key
    """
    return hashlib.sha256(key.encode()).hexdigest()
