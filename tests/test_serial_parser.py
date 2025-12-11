"""Tests for serial parser utilities."""

from nolongerevil.lib.serial_parser import (
    extract_serial_from_basic_auth,
    sanitize_serial,
)


class TestSanitizeSerial:
    """Tests for sanitize_serial function."""

    def test_valid_serial(self):
        """Test valid serial sanitization."""
        assert sanitize_serial("ABC123DEF456") == "ABC123DEF456"

    def test_lowercase_conversion(self):
        """Test lowercase to uppercase conversion."""
        assert sanitize_serial("abc123def456") == "ABC123DEF456"

    def test_removes_special_chars(self):
        """Test removal of special characters."""
        assert sanitize_serial("ABC-123-DEF-456") == "ABC123DEF456"

    def test_too_short(self):
        """Test rejection of too short serial."""
        assert sanitize_serial("ABC123") is None

    def test_empty_string(self):
        """Test rejection of empty string."""
        assert sanitize_serial("") is None

    def test_none_input(self):
        """Test handling of None input."""
        assert sanitize_serial(None) is None

    def test_whitespace_only(self):
        """Test rejection of whitespace only."""
        assert sanitize_serial("   ") is None


class TestExtractSerialFromBasicAuth:
    """Tests for extract_serial_from_basic_auth function."""

    def test_valid_basic_auth(self):
        """Test extraction from valid Basic Auth header."""
        # "ABC123DEF456:password" base64 encoded
        import base64

        encoded = base64.b64encode(b"ABC123DEF456:password").decode()
        header = f"Basic {encoded}"

        assert extract_serial_from_basic_auth(header) == "ABC123DEF456"

    def test_invalid_prefix(self):
        """Test rejection of non-Basic auth."""
        assert extract_serial_from_basic_auth("Bearer token123") is None

    def test_invalid_base64(self):
        """Test handling of invalid base64."""
        assert extract_serial_from_basic_auth("Basic not-valid-base64!!!") is None

    def test_short_serial_in_auth(self):
        """Test rejection of short serial in auth."""
        import base64

        encoded = base64.b64encode(b"ABC:password").decode()
        header = f"Basic {encoded}"

        assert extract_serial_from_basic_auth(header) is None
