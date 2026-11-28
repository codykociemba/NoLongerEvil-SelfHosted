"""Temperature safety bounds enforcement."""

from typing import Any

from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import TemperatureSafetyBounds

logger = get_logger(__name__)

# Default safety bounds
DEFAULT_MIN_CELSIUS = 7.222  # 45F
DEFAULT_MAX_CELSIUS = 35.0  # 95F


def get_safety_bounds(
    device_value: dict[str, Any] | None = None,
    shared_value: dict[str, Any] | None = None,
) -> TemperatureSafetyBounds:
    """Get temperature safety bounds from device or shared object.

    Args:
        device_value: Device object value dict
        shared_value: Shared object value dict

    Returns:
        Temperature safety bounds
    """
    bounds = TemperatureSafetyBounds()

    # Check device object for bounds
    if device_value:
        if "safety_temp_min" in device_value:
            bounds.min_celsius = float(device_value["safety_temp_min"])
        if "safety_temp_max" in device_value:
            bounds.max_celsius = float(device_value["safety_temp_max"])

    # Check shared object for bounds (takes precedence)
    if shared_value:
        if "safety_temp_min" in shared_value:
            bounds.min_celsius = float(shared_value["safety_temp_min"])
        if "safety_temp_max" in shared_value:
            bounds.max_celsius = float(shared_value["safety_temp_max"])

    return bounds


def clamp_temperature(
    temperature: float,
    bounds: TemperatureSafetyBounds | None = None,
    serial: str | None = None,
) -> float:
    """Clamp a temperature value to safety bounds.

    Args:
        temperature: Temperature in Celsius
        bounds: Safety bounds (uses defaults if None)
        serial: Device serial for logging

    Returns:
        Clamped temperature value
    """
    if bounds is None:
        bounds = TemperatureSafetyBounds()

    original = temperature

    if temperature < bounds.min_celsius:
        temperature = bounds.min_celsius
        logger.warning(
            f"Clamped temperature from {original:.2f}C to min {bounds.min_celsius:.2f}C"
            + (f" for device {serial}" if serial else "")
        )
    elif temperature > bounds.max_celsius:
        temperature = bounds.max_celsius
        logger.warning(
            f"Clamped temperature from {original:.2f}C to max {bounds.max_celsius:.2f}C"
            + (f" for device {serial}" if serial else "")
        )

    return temperature


def validate_and_clamp_temperatures(
    values: dict[str, Any],
    bounds: TemperatureSafetyBounds | None = None,
    serial: str | None = None,
) -> dict[str, Any]:
    """Validate and clamp all temperature fields in a values dict.

    Args:
        values: Values dictionary with potential temperature fields
        bounds: Safety bounds (uses defaults if None)
        serial: Device serial for logging

    Returns:
        Values dict with clamped temperatures
    """
    if bounds is None:
        bounds = TemperatureSafetyBounds()

    # Temperature fields to check
    temp_fields = [
        "target_temperature",
        "target_temperature_high",
        "target_temperature_low",
        "away_temperature_high",
        "away_temperature_low",
    ]

    result = values.copy()

    for field in temp_fields:
        if field in result and isinstance(result[field], (int, float)):
            result[field] = clamp_temperature(
                float(result[field]),
                bounds,
                serial,
            )

    return result


def celsius_to_fahrenheit(celsius: float) -> float:
    """Convert Celsius to Fahrenheit.

    Args:
        celsius: Temperature in Celsius

    Returns:
        Temperature in Fahrenheit
    """
    return (celsius * 9 / 5) + 32


def fahrenheit_to_celsius(fahrenheit: float) -> float:
    """Convert Fahrenheit to Celsius.

    Args:
        fahrenheit: Temperature in Fahrenheit

    Returns:
        Temperature in Celsius
    """
    return (fahrenheit - 32) * 5 / 9
