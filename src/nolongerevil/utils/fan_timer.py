"""Fan timer state preservation utility."""

import time
from typing import Any

from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import FanTimerState

logger = get_logger(__name__)

# How long after the server itself sets a fan timer a thermostat report of
# timeout=0/control=false is treated as a stale echo rather than a manual
# fan-off on the device.
SERVER_FAN_TIMER_ECHO_WINDOW_SECONDS = 60

# serial -> (fan_timer_timeout the server set, monotonic time it was set)
_server_fan_timers: dict[str, tuple[int, float]] = {}


def record_server_fan_timer(serial: str, values: dict[str, Any]) -> None:
    """Remember a fan timer that the server itself just wrote for a device.

    Args:
        serial: Device serial
        values: Values the server wrote to the device bucket
    """
    if "fan_timer_timeout" not in values:
        return
    try:
        timeout = int(values["fan_timer_timeout"])
    except (TypeError, ValueError, OverflowError):
        _server_fan_timers.pop(serial, None)
        return
    if timeout > int(time.time()):
        _server_fan_timers[serial] = (timeout, time.monotonic())
    else:
        _server_fan_timers.pop(serial, None)


def _in_server_echo_window(serial: str | None, timeout: int | None) -> bool:
    """Check if the active timer was set by the server within the echo window."""
    if serial is None or timeout is None:
        return False
    entry = _server_fan_timers.get(serial)
    if entry is None:
        return False
    set_timeout, set_at = entry
    if set_timeout != timeout:
        return False
    return time.monotonic() - set_at <= SERVER_FAN_TIMER_ECHO_WINDOW_SECONDS


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


def get_fan_timer_state(values: dict[str, Any]) -> FanTimerState:
    """Extract fan timer state from device values.

    Args:
        values: Device object values

    Returns:
        Fan timer state
    """
    timeout = values.get("fan_timer_timeout")
    return FanTimerState(timeout=int(timeout) if timeout is not None else None)


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

    # Get current fan timer state
    current_state = get_fan_timer_state(existing_values)

    if not is_fan_timer_active(current_state):
        # No active timer, nothing to preserve
        return result

    in_echo_window = _in_server_echo_window(serial, current_state.timeout)

    # Outside the short window after a server-set timer, a fan-off report is
    # a real change on the thermostat (for example the fan turned off there).
    if not in_echo_window and is_explicitly_turning_off_fan(new_values):
        logger.debug("Fan timer explicitly disabled" + (f" for device {serial}" if serial else ""))
        return result

    # This function is called only while merging thermostat-originated state.
    # Right after the server sets a timer, a device may echo its stale
    # pre-command fan fields before it applies the server push. Within that
    # window, preserve the active hold when the echo says timeout=0/control=false.
    # Explicit API/MQTT fan-off commands update server state through
    # execute_command() and do not pass here. A positive timeout from the
    # device is real progress and remains authoritative.
    fan_fields = extract_fan_timer_fields(existing_values)
    for key, value in fan_fields.items():
        if key not in new_values:
            result[key] = value

    if not in_echo_window:
        logger.debug(
            f"Preserving active fan timer (timeout={current_state.timeout})"
            + (f" for device {serial}" if serial else "")
        )
        return result

    incoming_timeout = new_values.get("fan_timer_timeout")
    if isinstance(incoming_timeout, (int, float, str)):
        try:
            normalized_timeout = int(incoming_timeout)
        except (ValueError, OverflowError):
            normalized_timeout = 0
    else:
        normalized_timeout = 0
    if normalized_timeout <= int(time.time()):
        result["fan_timer_timeout"] = current_state.timeout

    if existing_values.get("fan_control_state") is True:
        result["fan_control_state"] = True

    logger.debug(
        f"Preserving active fan timer (timeout={current_state.timeout})"
        + (f" for device {serial}" if serial else "")
    )

    return result
