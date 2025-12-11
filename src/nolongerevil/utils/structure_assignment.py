"""Structure ID assignment utility."""

from typing import Any

from nolongerevil.lib.logger import get_logger

logger = get_logger(__name__)


def derive_structure_id(user_id: str) -> str:
    """Derive a structure ID from a user ID.

    Strips the "user_" prefix if present to create a consistent
    structure identifier for multi-device grouping.

    Args:
        user_id: User identifier

    Returns:
        Structure ID
    """
    if user_id.startswith("user_"):
        return user_id[5:]  # Remove "user_" prefix
    return user_id


def assign_structure_id(
    values: dict[str, Any],
    owner_user_id: str | None,
    serial: str | None = None,
) -> dict[str, Any]:
    """Auto-assign structure_id to device values based on owner.

    Enables multi-device grouping by setting a consistent structure_id
    derived from the device owner's user ID.

    Args:
        values: Device object values
        owner_user_id: Device owner's user ID (may be None)
        serial: Device serial for logging

    Returns:
        Values with structure_id assigned
    """
    if not owner_user_id:
        return values

    # Only assign if not already set
    if "structure_id" in values and values["structure_id"]:
        return values

    result = values.copy()
    structure_id = derive_structure_id(owner_user_id)
    result["structure_id"] = structure_id

    logger.debug(
        f"Assigned structure_id={structure_id}" + (f" for device {serial}" if serial else "")
    )

    return result


def get_structure_id(values: dict[str, Any]) -> str | None:
    """Get the structure ID from device values.

    Args:
        values: Device object values

    Returns:
        Structure ID or None
    """
    return values.get("structure_id")


def needs_structure_id(values: dict[str, Any]) -> bool:
    """Check if device values need a structure ID assigned.

    Args:
        values: Device object values

    Returns:
        True if structure_id is missing or empty
    """
    structure_id = values.get("structure_id")
    return not structure_id
