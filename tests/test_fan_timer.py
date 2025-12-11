"""Tests for fan timer utilities."""

import time

from nolongerevil.lib.types import FanTimerState
from nolongerevil.utils.fan_timer import (
    get_fan_timer_state,
    is_fan_timer_active,
    preserve_fan_timer_state,
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

    def test_explicit_fan_off_overrides(self):
        """Test that explicit fan-off command overrides preservation."""
        future_timeout = int(time.time()) + 3600
        existing = {"fan_timer_timeout": future_timeout}
        new_values = {"fan_timer_timeout": 0}

        result = preserve_fan_timer_state(existing, new_values)

        assert result["fan_timer_timeout"] == 0

    def test_explicit_new_timeout(self):
        """Test that new timeout value is used."""
        existing = {"fan_timer_timeout": int(time.time()) + 3600}
        new_timeout = int(time.time()) + 7200
        new_values = {"fan_timer_timeout": new_timeout}

        result = preserve_fan_timer_state(existing, new_values)

        assert result["fan_timer_timeout"] == new_timeout
