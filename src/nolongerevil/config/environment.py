"""Environment configuration with validation."""

from pathlib import Path
from urllib.parse import urlparse, urlunparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Server configuration
    api_origin: str = Field(
        default="http://localhost",
        description="Base URL for thermostat connections",
    )
    server_host: str = Field(
        default="0.0.0.0",
        description="Host/IP to bind the server",
    )
    server_port: int = Field(
        default=443,
        description="Port for thermostat connections",
    )
    control_host: str = Field(
        default="0.0.0.0",
        description="Host/IP to bind control API server",
    )
    control_port: int = Field(
        default=8081,
        description="Port for control API (dashboard/automation)",
    )

    # TLS configuration
    cert_dir: str | None = Field(
        default=None,
        description="Directory containing TLS certificates",
    )

    # Entry key configuration
    entry_key_ttl_seconds: int = Field(
        default=3600,
        description="Pairing code expiration time in seconds",
    )

    # Weather configuration
    weather_cache_ttl_ms: int = Field(
        default=600000,
        description="Weather cache duration in milliseconds",
    )

    # Subscription configuration
    max_subscriptions_per_device: int = Field(
        default=100,
        description="Maximum concurrent subscriptions per device",
    )
    suspend_time_max: int = Field(
        default=600,
        ge=5,
        le=900,
        description="Maximum time (seconds) device will sleep before waking to resubscribe. "
        "This is a FALLBACK timer - server push wakes device instantly. "
        "Higher values = better battery life. Recommended: 600 seconds (10 minutes).",
    )
    defer_device_window: int = Field(
        default=15,
        ge=0,
        le=3599,
        description="X-nl-defer-device-window: Delay (seconds) before device sends PUT "
        "after local changes. Batches 'dial turning' jitter into single request. "
        "0 = disabled (immediate PUT on every change). Recommended: 15-30.",
    )
    disable_defer_window: int = Field(
        default=60,
        ge=0,
        le=3599,
        description="X-nl-disable-defer-window: After pushing updates, temporarily disable "
        "defer delay for this many seconds. Allows immediate confirmation. "
        "Only sent when server pushes temperature/mode changes.",
    )

    # Pairing configuration
    require_device_pairing: bool = Field(
        default=False,
        description="Require devices to complete registration before transport access. "
        "When False, any device can PUT and subscribe without pairing.",
    )

    # Debug configuration
    debug_logging: bool = Field(
        default=False,
        description="Enable detailed request/response logging",
    )
    debug_logs_dir: str = Field(
        default="./data/debug-logs",
        description="Directory for debug log files",
    )
    store_device_logs: bool = Field(
        default=False,
        description="Store uploaded device logs to disk",
    )

    # Database configuration
    sqlite3_db_path: str = Field(
        default="./data/database.sqlite",
        description="Path to SQLite3 database file",
    )

    # MQTT configuration (from environment variables set by run.sh)
    mqtt_host: str | None = Field(
        default=None,
        description="MQTT broker hostname",
    )
    mqtt_port: int = Field(
        default=1883,
        description="MQTT broker port",
    )
    mqtt_user: str | None = Field(
        default=None,
        description="MQTT username",
    )
    mqtt_password: str | None = Field(
        default=None,
        description="MQTT password",
    )
    mqtt_topic_prefix: str = Field(
        default="nolongerevil",
        description="Prefix for MQTT topics",
    )
    mqtt_discovery_prefix: str = Field(
        default="homeassistant",
        description="Home Assistant MQTT discovery prefix",
    )

    @property
    def mqtt_broker_url(self) -> str | None:
        """Get MQTT broker URL from host/port."""
        if not self.mqtt_host:
            return None
        return f"mqtt://{self.mqtt_host}:{self.mqtt_port}"

    @property
    def api_origin_with_port(self) -> str:
        """Get API origin with explicit port for device URLs.

        URLs without explicit ports may cause the device to fail port extraction,
        breaking TCP keepalive offload (WoWLAN). This ensures the port is always
        explicit in URLs sent to devices.
        """
        parsed = urlparse(self.api_origin)
        if parsed.port is None:
            netloc = f"{parsed.hostname}:{self.server_port}"
            return urlunparse(parsed._replace(netloc=netloc))
        return self.api_origin

    @property
    def weather_cache_ttl_seconds(self) -> float:
        """Get weather cache TTL in seconds."""
        return self.weather_cache_ttl_ms / 1000.0

    @property
    def connection_hold_timeout(self) -> float:
        """Maximum time to hold a chunked connection open.

        This should be LONGER than suspend_time_max because:
        1. Server should never close the connection before device wake timer fires
        2. Device wakes at suspend_time_max and resubscribes
        3. If server closes early, device loses the connection unnecessarily

        The +60 buffer accounts for network latency and clock skew.
        """
        return float(self.suspend_time_max + 60)

    @property
    def data_dir(self) -> Path:
        """Get the data directory path."""
        return Path(self.sqlite3_db_path).parent

    def ensure_data_dir(self) -> None:
        """Ensure the data directory exists."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.debug_logging:
            Path(self.debug_logs_dir).mkdir(parents=True, exist_ok=True)


settings = Settings()
