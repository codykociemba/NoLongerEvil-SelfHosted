"""MQTT integration constants."""

from typing import NamedTuple

from nolongerevil.lib.consts import HaMode


class TemperatureTopic(NamedTuple):
    """Temperature topic configuration.

    Attributes:
        topic_suffix: MQTT topic suffix and shared_values key
                      (e.g., "target_temperature" -> "{prefix}/{serial}/ha/target_temperature")
        discovery_key: HA discovery payload key prefix
                       (e.g., "temperature" -> "temperature_command_topic", "temperature_state_topic")
    """

    topic_suffix: str
    discovery_key: str


# Temperature topics for each mode
MODE_TEMPERATURE_TOPICS: dict[HaMode, tuple[TemperatureTopic, ...]] = {
    HaMode.OFF: (),  # No temperature topics when off
    HaMode.HEAT: (TemperatureTopic("target_temperature", "temperature"),),
    HaMode.COOL: (TemperatureTopic("target_temperature", "temperature"),),
    HaMode.HEAT_COOL: (
        TemperatureTopic("target_temperature_low", "temperature_low"),
        TemperatureTopic("target_temperature_high", "temperature_high"),
    ),
}

# All possible temperature topic suffixes (used for clearing stale topics on mode change)
ALL_TEMPERATURE_TOPIC_SUFFIXES: tuple[str, ...] = (
    "target_temperature",
    "target_temperature_low",
    "target_temperature_high",
)
