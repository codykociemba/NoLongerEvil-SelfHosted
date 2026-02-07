"""Tests for configuration module."""

from nolongerevil.config.environment import Settings


class TestSettings:
    """Tests for Settings class."""

    def test_default_values(self):
        """Test default configuration values."""
        settings = Settings()

        assert settings.api_origin == "http://localhost"
        assert settings.server_port == 443
        assert settings.control_port == 8081
        assert settings.entry_key_ttl_seconds == 3600
        assert settings.weather_cache_ttl_ms == 600000
        assert settings.max_subscriptions_per_device == 100
        assert settings.debug_logging is False

    def test_weather_cache_ttl_seconds(self):
        """Test weather cache TTL conversion."""
        settings = Settings(weather_cache_ttl_ms=300000)
        assert settings.weather_cache_ttl_seconds == 300.0

    def test_data_dir_property(self):
        """Test data directory property."""
        settings = Settings(sqlite3_db_path="./data/test.sqlite")
        assert settings.data_dir.name == "data"

    def test_env_override(self, monkeypatch):
        """Test environment variable override."""
        monkeypatch.setenv("SERVER_PORT", "8443")
        monkeypatch.setenv("DEBUG_LOGGING", "true")

        # Need to create new instance to pick up env vars
        settings = Settings()

        assert settings.server_port == 8443
        assert settings.debug_logging is True
