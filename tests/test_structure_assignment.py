"""Tests for structure assignment utility."""

from nolongerevil.utils.structure_assignment import (
    assign_structure_id,
    derive_structure_id,
    get_structure_id,
)


class TestDeriveStructureId:
    """Tests for derive_structure_id function."""

    def test_strips_user_prefix(self):
        """Test that user_ prefix is stripped."""
        assert derive_structure_id("user_abc123") == "abc123"

    def test_preserves_id_without_prefix(self):
        """Test that IDs without prefix are preserved."""
        assert derive_structure_id("abc123") == "abc123"

    def test_only_strips_at_start(self):
        """Test that user_ is only stripped from start."""
        assert derive_structure_id("my_user_id") == "my_user_id"

    def test_empty_after_prefix(self):
        """Test handling of just 'user_' prefix."""
        assert derive_structure_id("user_") == ""


class TestAssignStructureId:
    """Tests for assign_structure_id function."""

    def test_assigns_structure_id(self):
        """Test that structure_id is assigned from owner."""
        values = {"temperature": 21.5}
        result = assign_structure_id(values, "user_owner123", "SERIAL")
        assert result["structure_id"] == "owner123"
        assert result["temperature"] == 21.5

    def test_does_not_modify_original(self):
        """Test that original dict is not modified."""
        values = {"temperature": 21.5}
        result = assign_structure_id(values, "user_owner123", "SERIAL")
        assert "structure_id" not in values
        assert "structure_id" in result

    def test_preserves_existing_structure_id(self):
        """Test that existing structure_id is not overwritten."""
        values = {"structure_id": "existing_id"}
        result = assign_structure_id(values, "user_owner123", "SERIAL")
        assert result["structure_id"] == "existing_id"

    def test_no_owner_returns_unchanged(self):
        """Test that None owner returns values unchanged."""
        values = {"temperature": 21.5}
        result = assign_structure_id(values, None, "SERIAL")
        assert result == values
        assert "structure_id" not in result

    def test_empty_string_owner_returns_unchanged(self):
        """Test that empty string owner returns values unchanged."""
        values = {"temperature": 21.5}
        result = assign_structure_id(values, "", "SERIAL")
        assert result == values

    def test_serial_is_optional(self):
        """Test that serial parameter is optional."""
        values = {"temperature": 21.5}
        result = assign_structure_id(values, "user_owner123")
        assert result["structure_id"] == "owner123"

    def test_empty_structure_id_is_overwritten(self):
        """Test that empty string structure_id is overwritten."""
        values = {"structure_id": ""}
        result = assign_structure_id(values, "user_owner123", "SERIAL")
        # Empty string is falsy, so it should be overwritten
        assert result["structure_id"] == "owner123"


class TestGetStructureId:
    """Tests for get_structure_id function."""

    def test_returns_structure_id(self):
        """Test that structure_id is returned."""
        values = {"structure_id": "abc123"}
        assert get_structure_id(values) == "abc123"

    def test_returns_none_if_missing(self):
        """Test that None is returned if structure_id is missing."""
        values = {"temperature": 21.5}
        assert get_structure_id(values) is None

    def test_returns_empty_string(self):
        """Test that empty string is returned as-is."""
        values = {"structure_id": ""}
        assert get_structure_id(values) == ""
