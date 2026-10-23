"""Tests for serial parser utilities."""

from nolongerevil.lib.serial_parser import (
    extract_serial_from_basic_auth,
    extract_serial_from_session,
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


class TestExtractSerialFromSession:
    """Tests for extract_serial_from_session function."""

    MAC = "11b2334455d6"
    SERIAL = "02AA01AB501203EQ"

    def test_extracts_serial_with_lowercase_mac(self):
        """Session ID is <mac><serial>, mac matches lowercase exactly."""
        session_id = self.MAC + self.SERIAL
        assert extract_serial_from_session(session_id, self.MAC) == self.SERIAL

    def test_extracts_serial_with_uppercase_mac_argument(self):
        """MAC argument may be uppercase; comparison is case-insensitive."""
        session_id = self.MAC + self.SERIAL
        assert extract_serial_from_session(session_id, self.MAC.upper()) == self.SERIAL

    def test_extracts_serial_with_uppercase_session_prefix(self):
        """Session ID's MAC prefix may be uppercase."""
        session_id = self.MAC.upper() + self.SERIAL
        assert extract_serial_from_session(session_id, self.MAC) == self.SERIAL

    def test_mac_with_colons_is_normalized(self):
        """MAC argument with colon separators is stripped before matching."""
        mac_with_colons = "11:b2:33:44:55:d6"
        session_id = self.MAC + self.SERIAL
        assert extract_serial_from_session(session_id, mac_with_colons) == self.SERIAL

    def test_session_does_not_start_with_mac(self):
        """Session ID without the MAC prefix yields None."""
        session_id = "ffffffffffff" + self.SERIAL
        assert extract_serial_from_session(session_id, self.MAC) is None

    def test_session_id_equals_mac_exactly(self):
        """Nothing left after stripping the MAC prefix yields None."""
        assert extract_serial_from_session(self.MAC, self.MAC) is None

    def test_mac_too_short(self):
        """MAC shorter than 12 hex chars yields None."""
        assert extract_serial_from_session(self.MAC + self.SERIAL, "11b2334455") is None

    def test_mac_too_long(self):
        """MAC longer than 12 hex chars yields None."""
        assert extract_serial_from_session(self.MAC + self.SERIAL, self.MAC + "ab") is None

    def test_empty_session_id(self):
        """Empty session ID yields None."""
        assert extract_serial_from_session("", self.MAC) is None

    def test_empty_mac(self):
        """Empty MAC yields None."""
        assert extract_serial_from_session(self.MAC + self.SERIAL, "") is None

    def test_extracted_serial_is_uppercased(self):
        """A lowercase suffix is normalized to uppercase, like other serial sources."""
        session_id = self.MAC + self.SERIAL.lower()
        assert extract_serial_from_session(session_id, self.MAC) == self.SERIAL

    def test_extracted_serial_strips_invalid_characters(self):
        """Non-alphanumeric characters (e.g. path separators) are stripped so
        the result can't be used to escape state keys / MQTT topic paths."""
        session_id = self.MAC + "02AA01/../AB501203EQ"
        assert extract_serial_from_session(session_id, self.MAC) == self.SERIAL

    def test_extracted_serial_too_short_after_sanitization(self):
        """If sanitization leaves fewer than MIN_SERIAL_LENGTH chars, yields None."""
        session_id = self.MAC + "../.."
        assert extract_serial_from_session(session_id, self.MAC) is None

    def test_extracted_serial_all_invalid_characters(self):
        """A suffix made entirely of separators yields None, not an empty serial."""
        session_id = self.MAC + "////////////"
        assert extract_serial_from_session(session_id, self.MAC) is None
