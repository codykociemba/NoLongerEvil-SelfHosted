"""Fan timer state preservation utility."""

import time
from typing import Any

from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import FanTimerState

logger = get_logger(__name__)


def get_fan_timer_state(values: dict[str, Any]) -> FanTimerState:
    """Extract fan timer state from device values.

    Args:
        values: Device object values

    Returns:
        Fan timer state
    """
    timeout = values.get("fan_timer_timeout")
    return FanTimerState(timeout=int(timeout) if timeout is not None else None)


def is_explicitly_turning_off_fan(new_values: dict[str, Any]) -> bool:
    """Check if values explicitly turn off the fan.

    Args:
        new_values: Incoming device values

    Returns:
        True if explicitly disabling fan
    """
    # Check fan_timer_timeout = 0
    if "fan_timer_timeout" in new_values and new_values["fan_timer_timeout"] == 0:
        return True

    # Check fan_control_state = false
    return "fan_control_state" in new_values and new_values["fan_control_state"] is False


def is_fan_timer_active(state: FanTimerState) -> bool:
    """Check if a fan timer is currently active.

    Args:
        state: Fan timer state

    Returns:
        True if fan timer is active
    """
    if state.timeout is None:
        return False

    current_time = int(time.time())
    return state.timeout > current_time


def extract_fan_timer_fields(existing_values: dict[str, Any]) -> dict[str, Any]:
    """Extract all fan-related fields from device values.

    Args:
        existing_values: Device values

    Returns:
        Dictionary of fan-related fields
    """
    fan_fields = {}
    fan_keys = [
        "fan_timer_timeout",
        "fan_control_state",
        "fan_timer_duration",
        "fan_current_speed",
        "fan_mode",
    ]

    for key in fan_keys:
        if key in existing_values:
            fan_fields[key] = existing_values[key]

    return fan_fields


def preserve_fan_timer_state(
    existing_values: dict[str, Any] | None,
    new_values: dict[str, Any],
    serial: str | None = None,
) -> dict[str, Any]:
    """Preserve active fan timer state during device updates.

    Critical for Nest protocol compliance. When a fan timer is active,
    we must not override it unless explicitly requested.

    If fan timer is active (timeout > now), preserve all fan-related fields
    even if device doesn't include them, unless explicitly turned off.

    Args:
        existing_values: Current device values (may be None)
        new_values: Incoming values to apply
        serial: Device serial for logging

    Returns:
        Values with fan timer state preserved if appropriate
    """
    if not existing_values:
        return new_values

    result = new_values.copy()

    # Check if explicitly turning off fan
    if is_explicitly_turning_off_fan(new_values):
        logger.debug("Fan timer explicitly disabled" + (f" for device {serial}" if serial else ""))
        return result

    # Get current fan timer state
    current_state = get_fan_timer_state(existing_values)

    if not is_fan_timer_active(current_state):
        # No active timer, nothing to preserve
        return result

    # Preserve all fan-related fields from existing values
    # Only if not explicitly being set in new values
    fan_fields = extract_fan_timer_fields(existing_values)
    for key, value in fan_fields.items():
        if key not in new_values:
            result[key] = value

    logger.debug(
        f"Preserving active fan timer (timeout={current_state.timeout})"
        + (f" for device {serial}" if serial else "")
    )

    return result
