"""Tests for temperature safety utilities."""

from nolongerevil.lib.types import TemperatureSafetyBounds
from nolongerevil.utils.temperature_safety import (
    celsius_to_fahrenheit,
    clamp_temperature,
    fahrenheit_to_celsius,
    get_safety_bounds,
    validate_and_clamp_temperatures,
)


class TestClampTemperature:
    """Tests for clamp_temperature function."""

    def test_within_bounds(self):
        """Test temperature within bounds is unchanged."""
        bounds = TemperatureSafetyBounds(min_celsius=10.0, max_celsius=30.0)
        assert clamp_temperature(20.0, bounds) == 20.0

    def test_below_min(self):
        """Test temperature below minimum is clamped."""
        bounds = TemperatureSafetyBounds(min_celsius=10.0, max_celsius=30.0)
        assert clamp_temperature(5.0, bounds) == 10.0

    def test_above_max(self):
        """Test temperature above maximum is clamped."""
        bounds = TemperatureSafetyBounds(min_celsius=10.0, max_celsius=30.0)
        assert clamp_temperature(35.0, bounds) == 30.0

    def test_at_min_boundary(self):
        """Test temperature at minimum boundary."""
        bounds = TemperatureSafetyBounds(min_celsius=10.0, max_celsius=30.0)
        assert clamp_temperature(10.0, bounds) == 10.0

    def test_at_max_boundary(self):
        """Test temperature at maximum boundary."""
        bounds = TemperatureSafetyBounds(min_celsius=10.0, max_celsius=30.0)
        assert clamp_temperature(30.0, bounds) == 30.0

    def test_default_bounds(self):
        """Test with default bounds."""
        # Default bounds are 7.222C - 35C
        result = clamp_temperature(21.0)
        assert result == 21.0


class TestGetSafetyBounds:
    """Tests for get_safety_bounds function."""

    def test_default_bounds(self):
        """Test default bounds when no values provided."""
        bounds = get_safety_bounds()
        assert bounds.min_celsius == 7.222
        assert bounds.max_celsius == 35.0

    def test_device_values(self):
        """Test bounds from device values."""
        device = {"safety_temp_min": 15.0, "safety_temp_max": 28.0}
        bounds = get_safety_bounds(device_value=device)
        assert bounds.min_celsius == 15.0
        assert bounds.max_celsius == 28.0

    def test_shared_values_override(self):
        """Test shared values override device values."""
        device = {"safety_temp_min": 15.0, "safety_temp_max": 28.0}
        shared = {"safety_temp_min": 10.0, "safety_temp_max": 32.0}
        bounds = get_safety_bounds(device_value=device, shared_value=shared)
        assert bounds.min_celsius == 10.0
        assert bounds.max_celsius == 32.0


class TestValidateAndClampTemperatures:
    """Tests for validate_and_clamp_temperatures function."""

    def test_clamp_all_fields(self):
        """Test clamping of all temperature fields."""
        values = {
            "target_temperature": 5.0,
            "target_temperature_high": 40.0,
            "target_temperature_low": 3.0,
        }
        bounds = TemperatureSafetyBounds(min_celsius=10.0, max_celsius=30.0)

        result = validate_and_clamp_temperatures(values, bounds)

        assert result["target_temperature"] == 10.0
        assert result["target_temperature_high"] == 30.0
        assert result["target_temperature_low"] == 10.0

    def test_preserves_non_temperature_fields(self):
        """Test that non-temperature fields are preserved."""
        values = {
            "target_temperature": 20.0,
            "mode": "heat",
            "fan_timer_timeout": 12345,
        }

        result = validate_and_clamp_temperatures(values)

        assert result["mode"] == "heat"
        assert result["fan_timer_timeout"] == 12345


class TestTemperatureConversion:
    """Tests for temperature conversion functions."""

    def test_celsius_to_fahrenheit(self):
        """Test Celsius to Fahrenheit conversion."""
        assert celsius_to_fahrenheit(0) == 32
        assert celsius_to_fahrenheit(100) == 212
        assert abs(celsius_to_fahrenheit(21) - 69.8) < 0.1

    def test_fahrenheit_to_celsius(self):
        """Test Fahrenheit to Celsius conversion."""
        assert fahrenheit_to_celsius(32) == 0
        assert fahrenheit_to_celsius(212) == 100
        assert abs(fahrenheit_to_celsius(70) - 21.11) < 0.1
