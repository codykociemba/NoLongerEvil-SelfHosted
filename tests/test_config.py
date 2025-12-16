"""Tests for configuration module."""

from nolongerevil.config.environment import Settings


class TestSettings:
    """Tests for Settings class."""

    def test_default_values(self):
        """Test default configuration values."""
        settings = Settings()

        assert settings.api_origin == "https://backdoor.nolongerevil.com"
        assert settings.port == 8080
        assert settings.host == "0.0.0.0"
        assert settings.entry_key_ttl_seconds == 3600
        assert settings.weather_cache_ttl_ms == 600000
        assert settings.max_subscriptions_per_device == 100
        assert settings.debug_logging is False

    def test_default_workers(self):
        """Test default worker count is CPU-based."""
        import os

        settings = Settings()
        cpu_count = os.cpu_count() or 1
        expected = min(2 * cpu_count + 1, 8)
        assert settings.workers == expected

    def test_weather_cache_ttl_seconds(self):
        """Test weather cache TTL conversion."""
        settings = Settings(weather_cache_ttl_ms=300000)
        assert settings.weather_cache_ttl_seconds == 300.0

    def test_subscription_timeout_seconds_infinite(self):
        """Test infinite subscription timeout."""
        settings = Settings(subscription_timeout_ms=0)
        assert settings.subscription_timeout_seconds is None

    def test_subscription_timeout_seconds_finite(self):
        """Test finite subscription timeout."""
        settings = Settings(subscription_timeout_ms=30000)
        assert settings.subscription_timeout_seconds == 30.0

    def test_data_dir_property(self):
        """Test data directory property."""
        settings = Settings(sqlite3_db_path="./data/test.sqlite")
        assert settings.data_dir.name == "data"

    def test_env_override(self, monkeypatch):
        """Test environment variable override."""
        monkeypatch.setenv("PORT", "9000")
        monkeypatch.setenv("WORKERS", "4")
        monkeypatch.setenv("DEBUG_LOGGING", "true")

        # Need to create new instance to pick up env vars
        settings = Settings()

        assert settings.port == 9000
        assert settings.workers == 4
        assert settings.debug_logging is True
