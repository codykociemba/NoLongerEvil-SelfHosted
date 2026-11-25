"""SQLModel models and database engine configuration."""

from sqlmodel import SQLModel

# Import all models to ensure they're registered with SQLModel metadata
from nolongerevil.models.auth import APIKeyModel  # noqa: F401
from nolongerevil.models.control_auth import (  # noqa: F401
    ControlCredentialModel,
    ControlSessionModel,
)
from nolongerevil.models.device import DeviceObjectModel, LogModel, SessionModel  # noqa: F401
from nolongerevil.models.integration import IntegrationConfigModel, WeatherDataModel  # noqa: F401
from nolongerevil.models.sharing import DeviceShareInviteModel, DeviceShareModel  # noqa: F401
from nolongerevil.models.user import DeviceOwnerModel, EntryKeyModel, UserInfoModel  # noqa: F401

__all__ = [
    "SQLModel",
    # Device models
    "DeviceObjectModel",
    "SessionModel",
    "LogModel",
    # User models
    "UserInfoModel",
    "EntryKeyModel",
    "DeviceOwnerModel",
    # Auth models
    "APIKeyModel",
    "ControlCredentialModel",
    "ControlSessionModel",
    # Sharing models
    "DeviceShareModel",
    "DeviceShareInviteModel",
    # Integration models
    "IntegrationConfigModel",
    "WeatherDataModel",
]
