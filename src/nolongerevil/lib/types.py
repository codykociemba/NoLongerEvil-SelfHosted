"""Type definitions for nolongerevil server."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


@dataclass
class DeviceObject:
    """Represents a device state object."""

    serial: str
    object_key: str
    object_revision: int
    object_timestamp: int
    value: dict[str, Any]
    updated_at: datetime


@dataclass
class DeviceStateChange:
    """Represents a device state change event."""

    serial: str
    object_key: str
    old_value: dict[str, Any] | None
    new_value: dict[str, Any]
    changed_fields: list[str]
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class EntryKey:
    """Represents a device pairing entry key."""

    code: str
    serial: str
    created_at: datetime
    expires_at: datetime
    claimed_by: str | None = None
    claimed_at: datetime | None = None


@dataclass
class UserInfo:
    """Represents a user account."""

    clerk_id: str
    email: str
    created_at: datetime


@dataclass
class DeviceOwner:
    """Represents device ownership."""

    serial: str
    user_id: str
    created_at: datetime


@dataclass
class WeatherData:
    """Represents cached weather data."""

    postal_code: str
    country: str
    fetched_at: datetime
    data: dict[str, Any]


class DeviceSharePermission(Enum):
    """Permission levels for device sharing."""

    READ = "read"
    WRITE = "write"
    CONTROL = "control"
    ADMIN = "admin"


@dataclass
class DeviceShare:
    """Represents a device share between users."""

    owner_id: str
    shared_with_user_id: str
    serial: str
    permissions: DeviceSharePermission
    created_at: datetime


class DeviceShareInviteStatus(Enum):
    """Status of a device share invitation."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass
class DeviceShareInvite:
    """Represents a device share invitation."""

    invite_token: str
    owner_id: str
    email: str
    serial: str
    permissions: DeviceSharePermission
    status: DeviceShareInviteStatus
    invited_at: datetime
    expires_at: datetime
    accepted_at: datetime | None = None
    shared_with_user_id: str | None = None


@dataclass
class APIKeyPermissions:
    """Permissions for an API key."""

    devices: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=lambda: ["read", "write"])


@dataclass
class APIKey:
    """Represents an API authentication key."""

    id: str
    key_hash: str
    key_preview: str
    user_id: str
    name: str
    permissions: APIKeyPermissions
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None


@dataclass
class IntegrationConfig:
    """Configuration for a third-party integration."""

    user_id: str
    type: str
    enabled: bool
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass
class FanTimerState:
    """Fan timer state."""

    timeout: int | None = None


@dataclass
class TemperatureSafetyBounds:
    """Temperature safety bounds for a device."""

    min_heat: float = 4.5  # Celsius
    max_heat: float = 32.0
    min_cool: float = 9.0
    max_cool: float = 32.0
    # Generic min/max used by temperature_safety module
    min_celsius: float = 7.222  # 45F default
    max_celsius: float = 35.0  # 95F default
