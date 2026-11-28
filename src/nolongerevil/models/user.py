"""User-related SQLModel models."""

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


class UserInfoModel(SQLModel, table=True):
    """User account stored in the 'users' table."""

    __tablename__ = "users"

    clerkId: str = Field(primary_key=True)
    email: str
    createdAt: int  # Millisecond timestamp


class EntryKeyModel(SQLModel, table=True):
    """Device pairing entry key stored in the 'entryKeys' table."""

    __tablename__ = "entryKeys"

    code: str = Field(primary_key=True)
    serial: str
    createdAt: int  # Millisecond timestamp
    expiresAt: int  # Millisecond timestamp
    claimedBy: str | None = None
    claimedAt: int | None = None  # Millisecond timestamp

    __table_args__ = (Index("idx_entryKeys_serial", "serial"),)


class DeviceOwnerModel(SQLModel, table=True):
    """Device ownership stored in the 'deviceOwners' table."""

    __tablename__ = "deviceOwners"

    serial: str = Field(primary_key=True)
    userId: str
    createdAt: int  # Millisecond timestamp

    __table_args__ = (Index("idx_deviceOwners_userId", "userId"),)


class OpenModeOwnerModel(SQLModel, table=True):
    """Marks a deviceOwners row that open mode created automatically.

    These rows are removed at startup when device pairing is required, so a
    device that was never paired does not count as paired.
    """

    __tablename__ = "openModeOwners"

    serial: str = Field(primary_key=True)
    createdAt: int  # Millisecond timestamp
