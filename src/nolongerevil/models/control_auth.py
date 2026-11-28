"""Control panel login models."""

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


class ControlCredentialModel(SQLModel, table=True):
    """The control panel password hash (a single row)."""

    __tablename__ = "controlCredential"

    id: int = Field(default=1, primary_key=True)
    passwordHash: str
    updatedAt: int  # Millisecond timestamp


class ControlSessionModel(SQLModel, table=True):
    """A signed-in browser. Only a hash of the cookie token is stored."""

    __tablename__ = "controlSessions"

    id: int | None = Field(default=None, primary_key=True)
    tokenHash: str = Field(unique=True)
    name: str
    remember: bool
    createdAt: int  # Millisecond timestamp
    lastSeenAt: int  # Millisecond timestamp
    expiresAt: int  # Millisecond timestamp

    __table_args__ = (Index("idx_controlSessions_tokenHash", "tokenHash"),)
