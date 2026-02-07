"""Tests for revision/timestamp comparison logic in transport module.

Per the protocol spec (docs/protocol/server-rev-ts-guide.md):
- Timestamp alone determines which data is newer
- Revision is NOT used for sync decisions
- Zero timestamp means "no data" - always yields
- Equal timestamps = already synced, no action needed
"""

import pytest

from nolongerevil.routes.nest.transport import _is_server_newer


class TestIsServerNewer:
    """Test the _is_server_newer comparison function."""

    def test_client_ts_zero_means_resync(self) -> None:
        """When client has ts=0, server should always send data (client resyncing)."""
        assert _is_server_newer(server_ts=1000, client_ts=0) is True
        assert _is_server_newer(server_ts=0, client_ts=0) is True

    def test_server_ts_zero_means_no_data(self) -> None:
        """When server has ts=0 (no data), it has nothing to send."""
        assert _is_server_newer(server_ts=0, client_ts=1000) is False

    def test_server_timestamp_higher(self) -> None:
        """Server wins when its timestamp is higher."""
        assert _is_server_newer(server_ts=2000, client_ts=1000) is True
        assert _is_server_newer(server_ts=2001, client_ts=2000) is True

    def test_client_timestamp_higher(self) -> None:
        """Client wins when its timestamp is higher."""
        assert _is_server_newer(server_ts=1000, client_ts=2000) is False
        assert _is_server_newer(server_ts=2000, client_ts=2001) is False

    def test_equal_timestamp_means_synced(self) -> None:
        """When timestamps are equal, both sides are synced - no update needed.

        This is the critical fix: per the protocol spec, there is NO revision
        tiebreaker. Equal timestamps = already synced.
        """
        assert _is_server_newer(server_ts=1000, client_ts=1000) is False
        assert _is_server_newer(server_ts=5000, client_ts=5000) is False

    def test_realistic_millisecond_timestamps(self) -> None:
        """Test with realistic millisecond timestamps."""
        # Real-world ms timestamps
        ts_now = 1770147852122
        ts_earlier = 1770146554007

        assert _is_server_newer(server_ts=ts_now, client_ts=ts_earlier) is True
        assert _is_server_newer(server_ts=ts_earlier, client_ts=ts_now) is False

    def test_same_timestamp_different_situations(self) -> None:
        """Equal timestamps always means synced, regardless of context.

        Previously the code used revision as a tiebreaker, but per the protocol
        spec, when timestamps match both sides are considered synced.
        """
        # All should return False (synced state)
        assert _is_server_newer(server_ts=1000, client_ts=1000) is False
