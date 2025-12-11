"""Tests for MQTT integration helper functions."""

import time

from nolongerevil.integrations.mqtt.helpers import (
    battery_voltage_to_percent,
    celsius_to_fahrenheit,
    derive_hvac_action,
    fahrenheit_to_celsius,
    format_temperature,
    get_device_name,
    get_fan_mode,
    get_preset_mode,
    ha_mode_to_nest,
    is_device_away,
    is_eco_active,
    is_fan_running,
    nest_mode_to_ha,
)


class TestTemperatureConversions:
    """Tests for temperature conversion functions."""

    def test_celsius_to_fahrenheit_freezing(self):
        """Test conversion at freezing point."""
        assert celsius_to_fahrenheit(0) == 32

    def test_celsius_to_fahrenheit_boiling(self):
        """Test conversion at boiling point."""
        assert celsius_to_fahrenheit(100) == 212

    def test_celsius_to_fahrenheit_room_temp(self):
        """Test conversion at room temperature."""
        result = celsius_to_fahrenheit(21)
        assert abs(result - 69.8) < 0.01

    def test_fahrenheit_to_celsius_freezing(self):
        """Test conversion at freezing point."""
        assert fahrenheit_to_celsius(32) == 0

    def test_fahrenheit_to_celsius_boiling(self):
        """Test conversion at boiling point."""
        assert fahrenheit_to_celsius(212) == 100

    def test_fahrenheit_to_celsius_room_temp(self):
        """Test conversion at room temperature."""
        result = fahrenheit_to_celsius(70)
        assert abs(result - 21.11) < 0.01

    def test_roundtrip_conversion(self):
        """Test that conversions are reversible."""
        original = 25.5
        converted = fahrenheit_to_celsius(celsius_to_fahrenheit(original))
        assert abs(converted - original) < 0.0001


class TestModeConversions:
    """Tests for mode conversion functions."""

    def test_nest_mode_to_ha_off(self):
        """Test off mode conversion."""
        assert nest_mode_to_ha("off") == "off"

    def test_nest_mode_to_ha_heat(self):
        """Test heat mode conversion."""
        assert nest_mode_to_ha("heat") == "heat"

    def test_nest_mode_to_ha_cool(self):
        """Test cool mode conversion."""
        assert nest_mode_to_ha("cool") == "cool"

    def test_nest_mode_to_ha_range(self):
        """Test range mode conversion to heat_cool."""
        assert nest_mode_to_ha("range") == "heat_cool"

    def test_nest_mode_to_ha_heat_cool(self):
        """Test heat-cool mode conversion."""
        assert nest_mode_to_ha("heat-cool") == "heat_cool"

    def test_nest_mode_to_ha_none(self):
        """Test None input returns off."""
        assert nest_mode_to_ha(None) == "off"

    def test_nest_mode_to_ha_unknown(self):
        """Test unknown mode returns off."""
        assert nest_mode_to_ha("unknown_mode") == "off"

    def test_ha_mode_to_nest_off(self):
        """Test off mode conversion."""
        assert ha_mode_to_nest("off") == "off"

    def test_ha_mode_to_nest_heat(self):
        """Test heat mode conversion."""
        assert ha_mode_to_nest("heat") == "heat"

    def test_ha_mode_to_nest_cool(self):
        """Test cool mode conversion."""
        assert ha_mode_to_nest("cool") == "cool"

    def test_ha_mode_to_nest_heat_cool(self):
        """Test heat_cool mode conversion to range."""
        assert ha_mode_to_nest("heat_cool") == "range"

    def test_ha_mode_to_nest_none(self):
        """Test None input returns off."""
        assert ha_mode_to_nest(None) == "off"

    def test_ha_mode_to_nest_unknown(self):
        """Test unknown mode returns off."""
        assert ha_mode_to_nest("auto") == "off"


class TestDeriveHvacAction:
    """Tests for derive_hvac_action function."""

    def test_mode_off_returns_off(self):
        """Test that off mode returns off action."""
        device = {}
        shared = {"target_temperature_type": "off"}
        assert derive_hvac_action(device, shared) == "off"

    def test_heating_state(self):
        """Test that heater state returns heating."""
        device = {}
        shared = {"target_temperature_type": "heat", "hvac_heater_state": True}
        assert derive_hvac_action(device, shared) == "heating"

    def test_heating_x2_state(self):
        """Test that heat x2 state returns heating."""
        device = {}
        shared = {"target_temperature_type": "heat", "hvac_heat_x2_state": True}
        assert derive_hvac_action(device, shared) == "heating"

    def test_aux_heater_state(self):
        """Test that aux heater state returns heating."""
        device = {}
        shared = {"target_temperature_type": "heat", "hvac_aux_heater_state": True}
        assert derive_hvac_action(device, shared) == "heating"

    def test_cooling_state(self):
        """Test that AC state returns cooling."""
        device = {}
        shared = {"target_temperature_type": "cool", "hvac_ac_state": True}
        assert derive_hvac_action(device, shared) == "cooling"

    def test_cooling_x2_state(self):
        """Test that cool x2 state returns cooling."""
        device = {}
        shared = {"target_temperature_type": "cool", "hvac_cool_x2_state": True}
        assert derive_hvac_action(device, shared) == "cooling"

    def test_fan_timer_active(self):
        """Test that active fan timer returns fan."""
        future_time = int(time.time()) + 3600  # 1 hour from now
        device = {"fan_timer_timeout": future_time}
        shared = {"target_temperature_type": "heat"}
        assert derive_hvac_action(device, shared) == "fan"

    def test_fan_control_state(self):
        """Test that fan control state returns fan."""
        device = {"fan_control_state": True}
        shared = {"target_temperature_type": "heat"}
        assert derive_hvac_action(device, shared) == "fan"

    def test_idle_when_nothing_active(self):
        """Test that idle is returned when no states are active."""
        device = {}
        shared = {"target_temperature_type": "heat"}
        assert derive_hvac_action(device, shared) == "idle"

    def test_expired_fan_timer_returns_idle(self):
        """Test that expired fan timer returns idle."""
        past_time = int(time.time()) - 3600  # 1 hour ago
        device = {"fan_timer_timeout": past_time}
        shared = {"target_temperature_type": "heat"}
        assert derive_hvac_action(device, shared) == "idle"


class TestGetFanMode:
    """Tests for get_fan_mode function."""

    def test_fan_auto_by_default(self):
        """Test that auto is returned by default."""
        assert get_fan_mode({}) == "auto"

    def test_fan_on_with_control_state(self):
        """Test that on is returned with fan_control_state."""
        assert get_fan_mode({"fan_control_state": True}) == "on"

    def test_fan_on_with_active_timer(self):
        """Test that on is returned with active timer."""
        future_time = int(time.time()) + 3600
        assert get_fan_mode({"fan_timer_timeout": future_time}) == "on"

    def test_fan_auto_with_expired_timer(self):
        """Test that auto is returned with expired timer."""
        past_time = int(time.time()) - 3600
        assert get_fan_mode({"fan_timer_timeout": past_time}) == "auto"


class TestGetPresetMode:
    """Tests for get_preset_mode function."""

    def test_home_by_default(self):
        """Test that home is returned by default."""
        assert get_preset_mode({}, {}) == "home"

    def test_away_with_auto_away(self):
        """Test that away is returned with auto_away > 0."""
        assert get_preset_mode({"auto_away": 1}, {}) == "away"

    def test_away_with_away_flag(self):
        """Test that away is returned with away flag."""
        assert get_preset_mode({"away": True}, {}) == "away"

    def test_eco_with_leaf(self):
        """Test that eco is returned with leaf flag."""
        assert get_preset_mode({"leaf": True}, {}) == "eco"

    def test_eco_with_eco_leaf(self):
        """Test that eco is returned with eco.leaf."""
        assert get_preset_mode({"eco": {"leaf": True}}, {}) == "eco"

    def test_away_takes_precedence_over_eco(self):
        """Test that away takes precedence over eco."""
        assert get_preset_mode({"auto_away": 1, "leaf": True}, {}) == "away"


class TestGetDeviceName:
    """Tests for get_device_name function."""

    def test_label_takes_precedence(self):
        """Test that shared label is used first."""
        device = {"where_id": "00000000-0000-0000-0000-00010000000a"}
        shared = {"label": "My Thermostat", "name": "Nest"}
        assert get_device_name(device, shared, "SERIAL123") == "My Thermostat"

    def test_name_is_second_choice(self):
        """Test that shared name is used if no label."""
        device = {"where_id": "00000000-0000-0000-0000-00010000000a"}
        shared = {"name": "Nest Thermostat"}
        assert get_device_name(device, shared, "SERIAL123") == "Nest Thermostat"

    def test_where_id_lookup(self):
        """Test that where_id is looked up correctly."""
        device = {"where_id": "00000000-0000-0000-0000-00010000000a"}
        shared = {}
        assert get_device_name(device, shared, "SERIAL123") == "Kitchen"

    def test_where_id_living_room(self):
        """Test living room where_id lookup."""
        device = {"where_id": "00000000-0000-0000-0000-00010000000c"}
        shared = {}
        assert get_device_name(device, shared, "SERIAL123") == "Living Room"

    def test_serial_fallback(self):
        """Test that serial is used as fallback."""
        device = {"where_id": "unknown-id"}
        shared = {}
        assert get_device_name(device, shared, "SERIAL123") == "SERIAL123"

    def test_empty_inputs(self):
        """Test with empty inputs."""
        assert get_device_name({}, {}, "SERIAL123") == "SERIAL123"


class TestBatteryVoltageToPercent:
    """Tests for battery_voltage_to_percent function."""

    def test_full_battery(self):
        """Test full battery at 4.0V."""
        assert battery_voltage_to_percent(4.0) == 100

    def test_above_max_voltage(self):
        """Test voltage above max returns 100."""
        assert battery_voltage_to_percent(4.5) == 100

    def test_empty_battery(self):
        """Test empty battery at 3.5V."""
        assert battery_voltage_to_percent(3.5) == 0

    def test_below_min_voltage(self):
        """Test voltage below min returns 0."""
        assert battery_voltage_to_percent(3.0) == 0

    def test_mid_range_battery(self):
        """Test mid-range battery voltage."""
        # 3.75V is halfway between 3.5 and 4.0
        assert battery_voltage_to_percent(3.75) == 50

    def test_typical_battery(self):
        """Test typical battery voltage."""
        # 3.9V should be 80%
        assert battery_voltage_to_percent(3.9) == 80


class TestFormatTemperature:
    """Tests for format_temperature function."""

    def test_format_with_default_precision(self):
        """Test formatting with default precision."""
        assert format_temperature(21.5) == "21.5"

    def test_format_with_custom_precision(self):
        """Test formatting with custom precision."""
        assert format_temperature(21.567, precision=2) == "21.57"

    def test_format_none_returns_none(self):
        """Test that None input returns None."""
        assert format_temperature(None) is None

    def test_format_rounds_correctly(self):
        """Test that rounding works correctly."""
        assert format_temperature(21.55) == "21.6"


class TestBooleanStateChecks:
    """Tests for boolean state check functions."""

    def test_is_device_away_false_by_default(self):
        """Test is_device_away returns False by default."""
        assert is_device_away({}) is False

    def test_is_device_away_with_auto_away(self):
        """Test is_device_away with auto_away."""
        assert is_device_away({"auto_away": 1}) is True

    def test_is_device_away_with_away_flag(self):
        """Test is_device_away with away flag."""
        assert is_device_away({"away": True}) is True

    def test_is_device_away_auto_away_zero(self):
        """Test is_device_away with auto_away=0."""
        assert is_device_away({"auto_away": 0}) is False

    def test_is_fan_running_false_by_default(self):
        """Test is_fan_running returns False by default."""
        assert is_fan_running({}) is False

    def test_is_fan_running_true(self):
        """Test is_fan_running with hvac_fan_state."""
        assert is_fan_running({"hvac_fan_state": True}) is True

    def test_is_eco_active_false_by_default(self):
        """Test is_eco_active returns False by default."""
        assert is_eco_active({}) is False

    def test_is_eco_active_with_leaf(self):
        """Test is_eco_active with leaf flag."""
        assert is_eco_active({"leaf": True}) is True

    def test_is_eco_active_with_eco_leaf(self):
        """Test is_eco_active with eco.leaf."""
        assert is_eco_active({"eco": {"leaf": True}}) is True

    def test_is_eco_active_eco_not_dict(self):
        """Test is_eco_active when eco is not a dict."""
        assert is_eco_active({"eco": "not_a_dict"}) is False
