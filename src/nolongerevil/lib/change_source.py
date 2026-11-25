"""Who caused the current state change, for the history change log."""

from contextvars import ContextVar

# Set by the control API and MQTT handlers; device updates keep the default.
change_source: ContextVar[str] = ContextVar("change_source", default="thermostat")
