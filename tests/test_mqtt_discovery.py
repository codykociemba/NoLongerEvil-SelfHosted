from nolongerevil.integrations.mqtt.home_assistant_discovery import (
    build_climate_discovery_payload,
    build_occupancy_binary_sensor_discovery,
)


def payload(mode: str) -> dict:
    return build_climate_discovery_payload(
        "ABC123", "Hallway", "nolongerevil", {"target_temperature_type": mode}, {}
    )


def test_off_mode_advertises_setpoint():
    assert payload("off")["temperature_command_topic"] == (
        "nolongerevil/ABC123/ha/target_temperature/set"
    )


def test_min_temp_matches_40f_floor():
    assert payload("heat")["min_temp"] == 4.5


def test_home_away_is_presence():
    data = build_occupancy_binary_sensor_discovery("ABC123", "nolongerevil")
    assert data["device_class"] == "presence"
    assert data["unique_id"] == "nolongerevil_ABC123_occupancy"
