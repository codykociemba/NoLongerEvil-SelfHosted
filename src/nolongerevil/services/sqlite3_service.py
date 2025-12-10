"""SQLite3 implementation of device state persistence."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from nolongerevil.config import settings
from nolongerevil.lib.logger import get_logger
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
from nolongerevil.services.abstract_device_state_manager import AbstractDeviceStateManager

logger = get_logger(__name__)


class SQLite3Service(AbstractDeviceStateManager):
    """SQLite3 implementation of device state persistence."""

    def __init__(self, db_path: str | None = None) -> None:
        """Initialize the SQLite3 service.

        Args:
            db_path: Path to the database file. Defaults to settings value.
        """
        self.db_path = db_path or settings.sqlite3_db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Initialize the database connection and schema."""
        # Ensure directory exists
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row

        await self._create_schema()
        logger.info(f"SQLite3 database initialized at {self.db_path}")

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("SQLite3 database connection closed")

    @property
    def db(self) -> aiosqlite.Connection:
        """Get the database connection."""
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._db

    async def _create_schema(self) -> None:
        """Create the database schema."""
        await self.db.executescript("""
            -- Device state objects
            CREATE TABLE IF NOT EXISTS states (
                serial TEXT,
                object_key TEXT,
                object_revision INTEGER,
                object_timestamp INTEGER,
                value TEXT,
                updatedAt INTEGER
            );

            -- Request/response logs
            CREATE TABLE IF NOT EXISTS logs (
                ts INTEGER,
                route TEXT,
                serial TEXT,
                req TEXT,
                res TEXT
            );

            -- Device connection sessions
            CREATE TABLE IF NOT EXISTS sessions (
                serial TEXT,
                session TEXT,
                endpoint TEXT,
                startedAt INTEGER,
                lastActivity INTEGER,
                open INTEGER NOT NULL DEFAULT 0,
                client TEXT,
                meta TEXT,
                PRIMARY KEY (serial, session)
            );

            -- User accounts
            CREATE TABLE IF NOT EXISTS users (
                clerkId TEXT PRIMARY KEY,
                email TEXT,
                createdAt INTEGER
            );

            -- Device pairing codes
            CREATE TABLE IF NOT EXISTS entryKeys (
                code TEXT PRIMARY KEY,
                serial TEXT,
                createdAt INTEGER,
                expiresAt INTEGER,
                claimedBy INTEGER,
                claimedAt INTEGER
            );

            -- Device ownership
            CREATE TABLE IF NOT EXISTS deviceOwners (
                serial TEXT PRIMARY KEY,
                userId TEXT,
                createdAt INTEGER
            );

            -- Cached weather data
            CREATE TABLE IF NOT EXISTS weather (
                postalCode TEXT,
                country TEXT,
                fetchedAt INTEGER,
                data TEXT,
                PRIMARY KEY (postalCode, country)
            );

            -- Device access sharing
            CREATE TABLE IF NOT EXISTS deviceShares (
                ownerId TEXT,
                sharedWithUserId TEXT,
                serial TEXT,
                permissions TEXT,
                createdAt INTEGER,
                PRIMARY KEY (ownerId, sharedWithUserId, serial)
            );

            -- Device share invitations (note: typo matches TypeScript original)
            CREATE TABLE IF NOT EXISTS seviceShareInvites (
                ownerId TEXT,
                email TEXT,
                serial TEXT,
                permissions TEXT,
                status TEXT,
                inviteToken TEXT PRIMARY KEY,
                invitedAt INTEGER,
                acceptedAt INTEGER,
                expiresAt INTEGER,
                sharedWithUserId TEXT
            );

            -- API authentication keys
            CREATE TABLE IF NOT EXISTS apiKeys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyHash TEXT UNIQUE,
                keyPreview TEXT,
                userId TEXT,
                name TEXT,
                permissions TEXT,
                createdAt INTEGER,
                expiresAt INTEGER,
                lastUsedAt INTEGER
            );

            -- Third-party integrations
            CREATE TABLE IF NOT EXISTS integrations (
                userId TEXT,
                type TEXT,
                enabled INTEGER NOT NULL DEFAULT 0,
                config TEXT,
                createdAt INTEGER,
                updatedAt INTEGER,
                PRIMARY KEY (userId, type)
            );

            -- Indexes for performance
            CREATE INDEX IF NOT EXISTS idx_states_serial ON states(serial);
            CREATE INDEX IF NOT EXISTS idx_logs_serial ON logs(serial);
            CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
            CREATE INDEX IF NOT EXISTS idx_sessions_serial ON sessions(serial);
            CREATE INDEX IF NOT EXISTS idx_entryKeys_serial ON entryKeys(serial);
            CREATE INDEX IF NOT EXISTS idx_deviceOwners_userId ON deviceOwners(userId);
            CREATE INDEX IF NOT EXISTS idx_apiKeys_userId ON apiKeys(userId);
            CREATE INDEX IF NOT EXISTS idx_apiKeys_keyHash ON apiKeys(keyHash);
            CREATE INDEX IF NOT EXISTS idx_deviceShares_serial ON deviceShares(serial);
            CREATE INDEX IF NOT EXISTS idx_deviceShares_sharedWithUserId ON deviceShares(sharedWithUserId);
            CREATE INDEX IF NOT EXISTS idx_integrations_enabled ON integrations(enabled);
        """)
        await self.db.commit()

    def _timestamp_to_datetime(self, timestamp: int | None) -> datetime | None:
        """Convert Unix timestamp (milliseconds) to datetime."""
        if timestamp is None:
            return None
        # Timestamps are stored in milliseconds (JavaScript-style), convert to seconds
        return datetime.fromtimestamp(timestamp / 1000, tz=None)

    def _datetime_to_timestamp(self, value: datetime | None) -> int | None:
        """Convert datetime to Unix timestamp (milliseconds)."""
        if value is None:
            return None
        return int(value.timestamp() * 1000)

    def _now_timestamp(self) -> int:
        """Get current time as Unix timestamp (milliseconds)."""
        return int(datetime.now().timestamp() * 1000)

    # Device state operations
    async def get_object(self, serial: str, object_key: str) -> DeviceObject | None:
        """Get a single device object by serial and key."""
        async with self.db.execute(
            "SELECT * FROM states WHERE serial = ? AND object_key = ?",
            (serial, object_key),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return DeviceObject(
                serial=row["serial"],
                object_key=row["object_key"],
                object_revision=row["object_revision"],
                object_timestamp=row["object_timestamp"],
                value=json.loads(row["value"]),
                updated_at=self._timestamp_to_datetime(row["updatedAt"]) or datetime.now(),
            )

    async def get_objects_by_serial(self, serial: str) -> list[DeviceObject]:
        """Get all objects for a device."""
        async with self.db.execute("SELECT * FROM states WHERE serial = ?", (serial,)) as cursor:
            rows = await cursor.fetchall()
            return [
                DeviceObject(
                    serial=row["serial"],
                    object_key=row["object_key"],
                    object_revision=row["object_revision"],
                    object_timestamp=row["object_timestamp"],
                    value=json.loads(row["value"]),
                    updated_at=self._timestamp_to_datetime(row["updatedAt"]) or datetime.now(),
                )
                for row in rows
            ]

    async def get_all_objects(self) -> list[DeviceObject]:
        """Get all device objects."""
        async with self.db.execute("SELECT * FROM states") as cursor:
            rows = await cursor.fetchall()
            return [
                DeviceObject(
                    serial=row["serial"],
                    object_key=row["object_key"],
                    object_revision=row["object_revision"],
                    object_timestamp=row["object_timestamp"],
                    value=json.loads(row["value"]),
                    updated_at=self._timestamp_to_datetime(row["updatedAt"]) or datetime.now(),
                )
                for row in rows
            ]

    async def upsert_object(self, obj: DeviceObject) -> None:
        """Insert or update a device object."""
        now_ms = self._now_timestamp()
        # First try to update existing row
        cursor = await self.db.execute(
            """
            UPDATE states SET
                object_revision = ?,
                object_timestamp = ?,
                value = ?,
                updatedAt = ?
            WHERE serial = ? AND object_key = ?
            """,
            (
                obj.object_revision,
                obj.object_timestamp,
                json.dumps(obj.value),
                now_ms,
                obj.serial,
                obj.object_key,
            ),
        )
        # If no row was updated, insert a new one
        if cursor.rowcount == 0:
            await self.db.execute(
                """
                INSERT INTO states (serial, object_key, object_revision, object_timestamp, value, updatedAt)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    obj.serial,
                    obj.object_key,
                    obj.object_revision,
                    obj.object_timestamp,
                    json.dumps(obj.value),
                    now_ms,
                ),
            )
        await self.db.commit()

    async def delete_object(self, serial: str, object_key: str) -> bool:
        """Delete a device object."""
        cursor = await self.db.execute(
            "DELETE FROM states WHERE serial = ? AND object_key = ?",
            (serial, object_key),
        )
        await self.db.commit()
        return bool(cursor.rowcount > 0)

    async def delete_device(self, serial: str) -> int:
        """Delete all objects for a device."""
        cursor = await self.db.execute(
            "DELETE FROM states WHERE serial = ?",
            (serial,),
        )
        await self.db.commit()
        return cursor.rowcount

    # Entry key operations
    async def create_entry_key(self, entry_key: EntryKey) -> None:
        """Create a new entry key for device pairing."""
        await self.db.execute(
            """
            INSERT INTO entryKeys (code, serial, createdAt, expiresAt, claimedBy, claimedAt)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                entry_key.code,
                entry_key.serial,
                self._datetime_to_timestamp(entry_key.created_at),
                self._datetime_to_timestamp(entry_key.expires_at),
                entry_key.claimed_by,
                self._datetime_to_timestamp(entry_key.claimed_at),
            ),
        )
        await self.db.commit()

    async def get_entry_key(self, code: str) -> EntryKey | None:
        """Get an entry key by code."""
        async with self.db.execute("SELECT * FROM entryKeys WHERE code = ?", (code,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return EntryKey(
                code=row["code"],
                serial=row["serial"],
                created_at=self._timestamp_to_datetime(row["createdAt"]) or datetime.now(),
                expires_at=self._timestamp_to_datetime(row["expiresAt"]) or datetime.now(),
                claimed_by=row["claimedBy"],
                claimed_at=self._timestamp_to_datetime(row["claimedAt"]),
            )

    async def get_entry_key_by_serial(self, serial: str) -> EntryKey | None:
        """Get an unexpired entry key by serial."""
        now = self._now_timestamp()
        async with self.db.execute(
            """
            SELECT * FROM entryKeys
            WHERE serial = ? AND expiresAt > ? AND claimedBy IS NULL
            ORDER BY createdAt DESC LIMIT 1
            """,
            (serial, now),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return EntryKey(
                code=row["code"],
                serial=row["serial"],
                created_at=self._timestamp_to_datetime(row["createdAt"]) or datetime.now(),
                expires_at=self._timestamp_to_datetime(row["expiresAt"]) or datetime.now(),
                claimed_by=row["claimedBy"],
                claimed_at=self._timestamp_to_datetime(row["claimedAt"]),
            )

    async def claim_entry_key(self, code: str, user_id: str) -> bool:
        """Claim an entry key for a user."""
        now = self._now_timestamp()
        cursor = await self.db.execute(
            """
            UPDATE entryKeys
            SET claimedBy = ?, claimedAt = ?
            WHERE code = ? AND claimedBy IS NULL AND expiresAt > ?
            """,
            (user_id, now, code, now),
        )
        await self.db.commit()
        return bool(cursor.rowcount > 0)

    # User operations
    async def create_user(self, user: UserInfo) -> None:
        """Create a new user."""
        await self.db.execute(
            """
            INSERT INTO users (clerkId, email, createdAt)
            VALUES (?, ?, ?)
            ON CONFLICT(clerkId) DO UPDATE SET email = excluded.email
            """,
            (user.clerk_id, user.email, self._datetime_to_timestamp(user.created_at)),
        )
        await self.db.commit()

    async def get_user(self, clerk_id: str) -> UserInfo | None:
        """Get a user by clerk ID."""
        async with self.db.execute("SELECT * FROM users WHERE clerkId = ?", (clerk_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return UserInfo(
                clerk_id=row["clerkId"],
                email=row["email"],
                created_at=self._timestamp_to_datetime(row["createdAt"]) or datetime.now(),
            )

    async def get_user_by_email(self, email: str) -> UserInfo | None:
        """Get a user by email."""
        async with self.db.execute("SELECT * FROM users WHERE email = ?", (email,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return UserInfo(
                clerk_id=row["clerkId"],
                email=row["email"],
                created_at=self._timestamp_to_datetime(row["createdAt"]) or datetime.now(),
            )

    # Device owner operations
    async def set_device_owner(self, owner: DeviceOwner) -> None:
        """Set the owner of a device."""
        await self.db.execute(
            """
            INSERT INTO deviceOwners (serial, userId, createdAt)
            VALUES (?, ?, ?)
            ON CONFLICT(serial) DO UPDATE SET
                userId = excluded.userId,
                createdAt = excluded.createdAt
            """,
            (owner.serial, owner.user_id, self._datetime_to_timestamp(owner.created_at)),
        )
        await self.db.commit()

    async def get_device_owner(self, serial: str) -> DeviceOwner | None:
        """Get the owner of a device."""
        async with self.db.execute(
            "SELECT * FROM deviceOwners WHERE serial = ?", (serial,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return DeviceOwner(
                serial=row["serial"],
                user_id=row["userId"],
                created_at=self._timestamp_to_datetime(row["createdAt"]) or datetime.now(),
            )

    async def get_user_devices(self, user_id: str) -> list[str]:
        """Get all device serials owned by a user."""
        async with self.db.execute(
            "SELECT serial FROM deviceOwners WHERE userId = ?", (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [row["serial"] for row in rows]

    # Weather operations
    async def get_cached_weather(self, postal_code: str, country: str) -> WeatherData | None:
        """Get cached weather data."""
        async with self.db.execute(
            "SELECT * FROM weather WHERE postalCode = ? AND country = ?",
            (postal_code, country),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return WeatherData(
                postal_code=row["postalCode"],
                country=row["country"],
                fetched_at=self._timestamp_to_datetime(row["fetchedAt"]) or datetime.now(),
                data=json.loads(row["data"]),
            )

    async def cache_weather(self, weather: WeatherData) -> None:
        """Cache weather data."""
        await self.db.execute(
            """
            INSERT OR REPLACE INTO weather (postalCode, country, fetchedAt, data)
            VALUES (?, ?, ?, ?)
            """,
            (
                weather.postal_code,
                weather.country,
                self._datetime_to_timestamp(weather.fetched_at),
                json.dumps(weather.data),
            ),
        )
        await self.db.commit()

    # API key operations
    async def create_api_key(self, api_key: APIKey) -> None:
        """Create a new API key."""
        permissions_json = json.dumps(
            {
                "devices": api_key.permissions.devices,
                "scopes": api_key.permissions.scopes,
            }
        )
        await self.db.execute(
            """
            INSERT INTO apiKeys (keyHash, keyPreview, userId, name, permissions, createdAt, expiresAt, lastUsedAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                api_key.key_hash,
                api_key.key_preview,
                api_key.user_id,
                api_key.name,
                permissions_json,
                self._datetime_to_timestamp(api_key.created_at),
                self._datetime_to_timestamp(api_key.expires_at),
                self._datetime_to_timestamp(api_key.last_used_at),
            ),
        )
        await self.db.commit()

    async def get_api_key_by_hash(self, key_hash: str) -> APIKey | None:
        """Get an API key by its hash."""
        async with self.db.execute(
            "SELECT * FROM apiKeys WHERE keyHash = ?", (key_hash,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            permissions_data = json.loads(row["permissions"])
            return APIKey(
                id=str(row["id"]),
                key_hash=row["keyHash"],
                key_preview=row["keyPreview"],
                user_id=row["userId"],
                name=row["name"],
                permissions=APIKeyPermissions(
                    devices=permissions_data.get("devices", []),
                    scopes=permissions_data.get("scopes", ["read", "write"]),
                ),
                created_at=self._timestamp_to_datetime(row["createdAt"]) or datetime.now(),
                expires_at=self._timestamp_to_datetime(row["expiresAt"]),
                last_used_at=self._timestamp_to_datetime(row["lastUsedAt"]),
            )

    async def update_api_key_last_used(self, key_id: str) -> None:
        """Update the last used timestamp of an API key."""
        await self.db.execute(
            "UPDATE apiKeys SET lastUsedAt = ? WHERE id = ?",
            (self._now_timestamp(), key_id),
        )
        await self.db.commit()

    async def delete_api_key(self, key_id: str) -> bool:
        """Delete an API key."""
        cursor = await self.db.execute("DELETE FROM apiKeys WHERE id = ?", (key_id,))
        await self.db.commit()
        return bool(cursor.rowcount > 0)

    async def get_user_api_keys(self, user_id: str) -> list[APIKey]:
        """Get all API keys for a user."""
        async with self.db.execute("SELECT * FROM apiKeys WHERE userId = ?", (user_id,)) as cursor:
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                permissions_data = json.loads(row["permissions"])
                result.append(
                    APIKey(
                        id=str(row["id"]),
                        key_hash=row["keyHash"],
                        key_preview=row["keyPreview"],
                        user_id=row["userId"],
                        name=row["name"],
                        permissions=APIKeyPermissions(
                            devices=permissions_data.get("devices", []),
                            scopes=permissions_data.get("scopes", ["read", "write"]),
                        ),
                        created_at=self._timestamp_to_datetime(row["createdAt"]) or datetime.now(),
                        expires_at=self._timestamp_to_datetime(row["expiresAt"]),
                        last_used_at=self._timestamp_to_datetime(row["lastUsedAt"]),
                    )
                )
            return result

    # Device sharing operations
    async def create_device_share(self, share: DeviceShare) -> None:
        """Create a device share."""
        await self.db.execute(
            """
            INSERT INTO deviceShares (ownerId, sharedWithUserId, serial, permissions, createdAt)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(ownerId, sharedWithUserId, serial) DO UPDATE SET
                permissions = excluded.permissions
            """,
            (
                share.owner_id,
                share.shared_with_user_id,
                share.serial,
                share.permissions.value,
                self._datetime_to_timestamp(share.created_at),
            ),
        )
        await self.db.commit()

    async def get_device_shares(self, serial: str) -> list[DeviceShare]:
        """Get all shares for a device."""
        async with self.db.execute(
            "SELECT * FROM deviceShares WHERE serial = ?", (serial,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                DeviceShare(
                    owner_id=row["ownerId"],
                    shared_with_user_id=row["sharedWithUserId"],
                    serial=row["serial"],
                    permissions=DeviceSharePermission(row["permissions"]),
                    created_at=self._timestamp_to_datetime(row["createdAt"]) or datetime.now(),
                )
                for row in rows
            ]

    async def get_user_shared_devices(self, user_id: str) -> list[DeviceShare]:
        """Get all devices shared with a user."""
        async with self.db.execute(
            "SELECT * FROM deviceShares WHERE sharedWithUserId = ?", (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                DeviceShare(
                    owner_id=row["ownerId"],
                    shared_with_user_id=row["sharedWithUserId"],
                    serial=row["serial"],
                    permissions=DeviceSharePermission(row["permissions"]),
                    created_at=self._timestamp_to_datetime(row["createdAt"]) or datetime.now(),
                )
                for row in rows
            ]

    async def delete_device_share(
        self, owner_id: str, shared_with_user_id: str, serial: str
    ) -> bool:
        """Delete a device share."""
        cursor = await self.db.execute(
            "DELETE FROM deviceShares WHERE ownerId = ? AND sharedWithUserId = ? AND serial = ?",
            (owner_id, shared_with_user_id, serial),
        )
        await self.db.commit()
        return bool(cursor.rowcount > 0)

    # Device share invite operations
    async def create_device_share_invite(self, invite: DeviceShareInvite) -> None:
        """Create a device share invitation."""
        await self.db.execute(
            """
            INSERT INTO seviceShareInvites (
                ownerId, email, serial, permissions, status,
                inviteToken, invitedAt, expiresAt, acceptedAt, sharedWithUserId
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invite.owner_id,
                invite.email,
                invite.serial,
                invite.permissions.value,
                invite.status.value,
                invite.invite_token,
                self._datetime_to_timestamp(invite.invited_at),
                self._datetime_to_timestamp(invite.expires_at),
                self._datetime_to_timestamp(invite.accepted_at),
                invite.shared_with_user_id,
            ),
        )
        await self.db.commit()

    async def get_device_share_invite(self, invite_token: str) -> DeviceShareInvite | None:
        """Get an invitation by token."""
        async with self.db.execute(
            "SELECT * FROM seviceShareInvites WHERE inviteToken = ?", (invite_token,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return DeviceShareInvite(
                invite_token=row["inviteToken"],
                owner_id=row["ownerId"],
                email=row["email"],
                serial=row["serial"],
                permissions=DeviceSharePermission(row["permissions"]),
                status=DeviceShareInviteStatus(row["status"]),
                invited_at=self._timestamp_to_datetime(row["invitedAt"]) or datetime.now(),
                expires_at=self._timestamp_to_datetime(row["expiresAt"]) or datetime.now(),
                accepted_at=self._timestamp_to_datetime(row["acceptedAt"]),
                shared_with_user_id=row["sharedWithUserId"],
            )

    async def accept_device_share_invite(self, invite_token: str, user_id: str) -> bool:
        """Accept a device share invitation."""
        now = self._now_timestamp()
        cursor = await self.db.execute(
            """
            UPDATE seviceShareInvites
            SET status = 'accepted', acceptedAt = ?, sharedWithUserId = ?
            WHERE inviteToken = ? AND status = 'pending' AND expiresAt > ?
            """,
            (now, user_id, invite_token, now),
        )
        await self.db.commit()
        return bool(cursor.rowcount > 0)

    # Integration operations
    async def get_integrations(self, user_id: str) -> list[IntegrationConfig]:
        """Get all integrations for a user."""
        async with self.db.execute(
            "SELECT * FROM integrations WHERE userId = ?", (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                IntegrationConfig(
                    user_id=row["userId"],
                    type=row["type"],
                    enabled=bool(row["enabled"]),
                    config=json.loads(row["config"]),
                    created_at=self._timestamp_to_datetime(row["createdAt"]) or datetime.now(),
                    updated_at=self._timestamp_to_datetime(row["updatedAt"]) or datetime.now(),
                )
                for row in rows
            ]

    async def get_enabled_integrations(self) -> list[IntegrationConfig]:
        """Get all enabled integrations."""
        async with self.db.execute("SELECT * FROM integrations WHERE enabled = 1") as cursor:
            rows = await cursor.fetchall()
            return [
                IntegrationConfig(
                    user_id=row["userId"],
                    type=row["type"],
                    enabled=bool(row["enabled"]),
                    config=json.loads(row["config"]),
                    created_at=self._timestamp_to_datetime(row["createdAt"]) or datetime.now(),
                    updated_at=self._timestamp_to_datetime(row["updatedAt"]) or datetime.now(),
                )
                for row in rows
            ]

    async def upsert_integration(self, integration: IntegrationConfig) -> None:
        """Create or update an integration."""
        now_ms = int(integration.updated_at.timestamp() * 1000)
        created_ms = int(integration.created_at.timestamp() * 1000)
        await self.db.execute(
            """
            INSERT INTO integrations (userId, type, enabled, config, createdAt, updatedAt)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(userId, type) DO UPDATE SET
                enabled = excluded.enabled,
                config = excluded.config,
                updatedAt = excluded.updatedAt
            """,
            (
                integration.user_id,
                integration.type,
                1 if integration.enabled else 0,
                json.dumps(integration.config),
                created_ms,
                now_ms,
            ),
        )
        await self.db.commit()

    async def delete_integration(self, user_id: str, integration_type: str) -> bool:
        """Delete an integration."""
        cursor = await self.db.execute(
            "DELETE FROM integrations WHERE userId = ? AND type = ?",
            (user_id, integration_type),
        )
        await self.db.commit()
        return bool(cursor.rowcount > 0)

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
        now = self._now_timestamp()
        await self.db.execute(
            """
            INSERT INTO sessions (serial, session, endpoint, startedAt, lastActivity, open, client, meta)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(serial, session) DO UPDATE SET
                lastActivity = excluded.lastActivity,
                open = 1
            """,
            (
                serial,
                session_id,
                endpoint,
                now,
                now,
                client,
                json.dumps(meta) if meta else None,
            ),
        )
        await self.db.commit()

    async def update_session_activity(self, serial: str, session_id: str) -> None:
        """Update the last activity timestamp for a session."""
        await self.db.execute(
            "UPDATE sessions SET lastActivity = ? WHERE serial = ? AND session = ?",
            (self._now_timestamp(), serial, session_id),
        )
        await self.db.commit()

    async def close_session(self, serial: str, session_id: str) -> None:
        """Mark a session as closed."""
        await self.db.execute(
            "UPDATE sessions SET open = 0 WHERE serial = ? AND session = ?",
            (serial, session_id),
        )
        await self.db.commit()

    # Request logging
    async def log_request(
        self,
        route: str,
        serial: str | None,
        request_data: dict[str, Any],
        response_data: dict[str, Any],
    ) -> None:
        """Log a request/response pair."""
        await self.db.execute(
            "INSERT INTO logs (ts, route, serial, req, res) VALUES (?, ?, ?, ?, ?)",
            (
                self._now_timestamp(),
                route,
                serial,
                json.dumps(request_data),
                json.dumps(response_data),
            ),
        )
        await self.db.commit()

    # Additional methods from TypeScript AbstractDeviceStateManager

    async def generate_entry_key(
        self, serial: str, ttl_seconds: int = 3600
    ) -> dict[str, Any] | None:
        """Generate entry key for device pairing."""
        try:
            now_ms = self._now_timestamp()
            expires_at = now_ms + (ttl_seconds * 1000)

            # Delete all existing entry keys for this serial
            await self.db.execute("DELETE FROM entryKeys WHERE serial = ?", (serial,))

            # Generate unique code
            import random
            import string

            code = None
            for _ in range(20):
                digits = "".join(random.choices(string.digits, k=3))
                letters = "".join(random.choices(string.ascii_uppercase, k=4))
                candidate = f"{digits}{letters}"

                async with self.db.execute(
                    "SELECT code FROM entryKeys WHERE code = ?", (candidate,)
                ) as cursor:
                    if not await cursor.fetchone():
                        code = candidate
                        break

            if not code:
                logger.error(f"Unable to allocate entry key for {serial}")
                return None

            await self.db.execute(
                """
                INSERT INTO entryKeys (code, serial, createdAt, expiresAt)
                VALUES (?, ?, ?, ?)
                """,
                (code, serial, now_ms, expires_at),
            )
            await self.db.commit()

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
            now_ms = self._now_timestamp()
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
                            object_timestamp=now_ms,
                            value=updated_value,
                            updated_at=self._timestamp_to_datetime(now_ms) or datetime.now(),
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

            now_ms = self._now_timestamp()
            weather_data = {
                "current": weather.data.get("current"),
                "location": weather.data.get("location"),
                "updatedAt": now_ms,
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
                            object_timestamp=now_ms,
                            value=updated_value,
                            updated_at=self._timestamp_to_datetime(now_ms) or datetime.now(),
                        )
                    )
        except Exception as e:
            logger.error(f"Failed to sync weather for {user_id}: {e}")

    async def ensure_device_alert_dialog(self, serial: str) -> None:
        """Ensure device alert dialog exists."""
        try:
            now_ms = self._now_timestamp()
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
                        object_timestamp=now_ms,
                        value=dialog_value,
                        updated_at=self._timestamp_to_datetime(now_ms) or datetime.now(),
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
                        object_timestamp=now_ms,
                        value=default_user_state,
                        updated_at=self._timestamp_to_datetime(now_ms) or datetime.now(),
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
        async with self.db.execute(
            "SELECT userId, config FROM integrations WHERE type = 'mqtt' AND enabled = 1"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {"userId": row["userId"], "config": json.loads(row["config"] or "{}")}
                for row in rows
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
