"""Tests for fan timer utilities."""

import time

from nolongerevil.lib.types import FanTimerState
from nolongerevil.utils import fan_timer
from nolongerevil.utils.fan_timer import (
    SERVER_FAN_TIMER_ECHO_WINDOW_SECONDS,
    get_fan_timer_state,
    is_fan_timer_active,
    preserve_fan_timer_state,
    record_server_fan_timer,
)


class TestGetFanTimerState:
    """Tests for get_fan_timer_state function."""

    def test_with_timeout(self):
        """Test extraction of fan timer state."""
        values = {"fan_timer_timeout": 12345678}
        state = get_fan_timer_state(values)
        assert state.timeout == 12345678

    def test_without_timeout(self):
        """Test with no timeout set."""
        values = {"other_field": "value"}
        state = get_fan_timer_state(values)
        assert state.timeout is None


class TestIsFanTimerActive:
    """Tests for is_fan_timer_active function."""

    def test_active_timer(self):
        """Test with active timer (future timeout)."""
        future_time = int(time.time()) + 3600
        state = FanTimerState(timeout=future_time)
        assert is_fan_timer_active(state) is True

    def test_expired_timer(self):
        """Test with expired timer (past timeout)."""
        past_time = int(time.time()) - 3600
        state = FanTimerState(timeout=past_time)
        assert is_fan_timer_active(state) is False

    def test_no_timer(self):
        """Test with no timer set."""
        state = FanTimerState(timeout=None)
        assert is_fan_timer_active(state) is False


class TestPreserveFanTimerState:
    """Tests for preserve_fan_timer_state function."""

    def test_no_existing_values(self):
        """Test with no existing values."""
        new_values = {"target_temperature": 21.0}
        result = preserve_fan_timer_state(None, new_values)
        assert result == {"target_temperature": 21.0}

    def test_no_active_timer(self):
        """Test with no active timer."""
        existing = {"fan_timer_timeout": int(time.time()) - 3600}
        new_values = {"target_temperature": 21.0}
        result = preserve_fan_timer_state(existing, new_values)
        assert "fan_timer_timeout" not in result

    def test_preserves_active_timer(self):
        """Test that active timer is preserved."""
        future_timeout = int(time.time()) + 3600
        existing = {"fan_timer_timeout": future_timeout}
        new_values = {"target_temperature": 21.0}

        result = preserve_fan_timer_state(existing, new_values)

        assert result["fan_timer_timeout"] == future_timeout

    def test_device_fan_off_echo_does_not_override_active_server_timer(self):
        """A stale echo right after a server-set timer does not cancel it."""
        future_timeout = int(time.time()) + 3600
        record_server_fan_timer("SERIAL1", {"fan_timer_timeout": future_timeout})
        existing = {"fan_timer_timeout": future_timeout, "fan_control_state": True}
        new_values = {"fan_timer_timeout": 0, "fan_control_state": False}

        result = preserve_fan_timer_state(existing, new_values, "SERIAL1")

        assert result["fan_timer_timeout"] == future_timeout
        assert result["fan_control_state"] is True

    def test_explicit_new_timeout(self):
        """Test that new timeout value is used."""
        existing = {"fan_timer_timeout": int(time.time()) + 3600}
        new_timeout = int(time.time()) + 7200
        new_values = {"fan_timer_timeout": new_timeout}

        result = preserve_fan_timer_state(existing, new_values)

        assert result["fan_timer_timeout"] == new_timeout

    def test_explicit_new_timeout_as_numeric_string(self):
        """Test that a new numeric-string timeout is used."""
        existing = {"fan_timer_timeout": int(time.time()) + 3600}
        new_timeout = str(int(time.time()) + 7200)

        result = preserve_fan_timer_state(existing, {"fan_timer_timeout": new_timeout})

        assert result["fan_timer_timeout"] == new_timeout


class TestServerEchoWindow:
    """A fan-off report is a stale echo only right after a server-set timer."""

    def setup_method(self):
        fan_timer._server_fan_timers.clear()

    def test_device_fan_off_honoured_without_server_timer(self):
        future_timeout = int(time.time()) + 3600
        existing = {"fan_timer_timeout": future_timeout, "fan_control_state": True}
        new_values = {"fan_timer_timeout": 0, "fan_control_state": False}

        result = preserve_fan_timer_state(existing, new_values, "SERIAL1")

        assert result["fan_timer_timeout"] == 0
        assert result["fan_control_state"] is False

    def test_device_fan_off_honoured_after_window(self, monkeypatch):
        future_timeout = int(time.time()) + 3600
        record_server_fan_timer("SERIAL1", {"fan_timer_timeout": future_timeout})
        later = time.monotonic() + SERVER_FAN_TIMER_ECHO_WINDOW_SECONDS + 1
        monkeypatch.setattr(fan_timer.time, "monotonic", lambda: later)
        existing = {"fan_timer_timeout": future_timeout, "fan_control_state": True}
        new_values = {"fan_timer_timeout": 0, "fan_control_state": False}

        result = preserve_fan_timer_state(existing, new_values, "SERIAL1")

        assert result["fan_timer_timeout"] == 0
        assert result["fan_control_state"] is False

    def test_window_is_per_device(self):
        future_timeout = int(time.time()) + 3600
        record_server_fan_timer("SERIAL1", {"fan_timer_timeout": future_timeout})
        existing = {"fan_timer_timeout": future_timeout, "fan_control_state": True}

        result = preserve_fan_timer_state(existing, {"fan_timer_timeout": 0}, "SERIAL2")

        assert result["fan_timer_timeout"] == 0

    def test_server_fan_off_clears_window(self):
        future_timeout = int(time.time()) + 3600
        record_server_fan_timer("SERIAL1", {"fan_timer_timeout": future_timeout})
        record_server_fan_timer("SERIAL1", {"fan_timer_timeout": 0})
        existing = {"fan_timer_timeout": future_timeout, "fan_control_state": True}

        result = preserve_fan_timer_state(existing, {"fan_timer_timeout": 0}, "SERIAL1")

        assert result["fan_timer_timeout"] == 0
