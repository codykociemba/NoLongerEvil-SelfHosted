"""Tests for URL normalizer middleware."""

from nolongerevil.middleware.url_normalizer import normalize_url


class TestNormalizeUrl:
    """Tests for the normalize_url function."""

    def test_already_normalized_url(self):
        """Test that /nest/ prefixed URLs pass through unchanged."""
        assert normalize_url("/nest/entry") == "/nest/entry"
        assert normalize_url("/nest/transport/abc") == "/nest/transport/abc"

    def test_entry_endpoint(self):
        """Test /entry normalization."""
        assert normalize_url("/entry") == "/nest/entry"
        assert normalize_url("/entry/") == "/nest/entry"

    def test_ping_endpoint(self):
        """Test /ping normalization."""
        assert normalize_url("/ping") == "/nest/ping"
        assert normalize_url("/ping/") == "/nest/ping"

    def test_passphrase_endpoint(self):
        """Test /passphrase normalization."""
        assert normalize_url("/passphrase") == "/nest/passphrase"
        assert normalize_url("/passphrase/") == "/nest/passphrase"

    def test_czfe_endpoint(self):
        """Test /czfe/* normalization to /nest/transport/*."""
        assert normalize_url("/czfe/v5/subscribe") == "/nest/transport/v5/subscribe"
        assert normalize_url("/czfe/v5/put") == "/nest/transport/v5/put"

    def test_transport_endpoint(self):
        """Test /transport/* normalization."""
        assert normalize_url("/transport/v5/subscribe") == "/nest/transport/v5/subscribe"
        assert normalize_url("/transport/") == "/nest/transport/"

    def test_weather_endpoint(self):
        """Test /weather/* normalization."""
        assert normalize_url("/weather/v1") == "/nest/weather/v1"
        assert normalize_url("/weather/forecast") == "/nest/weather/forecast"

    def test_upload_endpoint(self):
        """Test /upload normalization."""
        assert normalize_url("/upload") == "/nest/upload"
        assert normalize_url("/upload/") == "/nest/upload"

    def test_pro_info_endpoint(self):
        """Test /pro_info/* normalization."""
        assert normalize_url("/pro_info/device123") == "/nest/pro_info/device123"

    def test_unmatched_url_passes_through(self):
        """Test that non-legacy URLs pass through unchanged."""
        assert normalize_url("/api/v1/status") == "/api/v1/status"
        assert normalize_url("/health") == "/health"
        assert normalize_url("/") == "/"

    def test_control_endpoints_not_normalized(self):
        """Test that control endpoints are not normalized."""
        assert normalize_url("/control/status") == "/control/status"
        assert normalize_url("/control/command") == "/control/command"
