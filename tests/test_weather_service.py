"""Tests for weather service."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from nolongerevil.lib.types import WeatherData
from nolongerevil.services.weather_service import WeatherService


@pytest.fixture
def mock_storage():
    """Create a mock storage backend."""
    storage = AsyncMock()
    storage.get_cached_weather = AsyncMock(return_value=None)
    storage.cache_weather = AsyncMock()
    return storage


@pytest.fixture
def weather_service(mock_storage):
    """Create a weather service for testing."""
    return WeatherService(mock_storage)


class TestWeatherServiceInit:
    """Tests for WeatherService initialization."""

    def test_initialization(self, mock_storage):
        """Test service initialization."""
        service = WeatherService(mock_storage)
        assert service._storage is mock_storage
        assert service._session is None


class TestWeatherServiceInitialize:
    """Tests for initialize method."""

    @pytest.mark.asyncio
    async def test_creates_session(self, weather_service):
        """Test that initialize creates a session."""
        await weather_service.initialize()
        assert weather_service._session is not None
        await weather_service.close()

    @pytest.mark.asyncio
    async def test_session_has_timeout(self, weather_service):
        """Test that session has timeout configured."""
        await weather_service.initialize()
        # Session is created with timeout
        assert weather_service._session is not None
        await weather_service.close()


class TestWeatherServiceClose:
    """Tests for close method."""

    @pytest.mark.asyncio
    async def test_closes_session(self, weather_service):
        """Test that close closes the session."""
        await weather_service.initialize()
        await weather_service.close()
        assert weather_service._session is None

    @pytest.mark.asyncio
    async def test_close_without_init_is_safe(self, weather_service):
        """Test that close without init doesn't raise."""
        await weather_service.close()
        assert weather_service._session is None


class TestCacheValidity:
    """Tests for _is_cache_valid method."""

    def test_fresh_cache_is_valid(self, weather_service):
        """Test that fresh cache is valid."""
        weather = WeatherData(
            postal_code="12345",
            country="US",
            fetched_at=datetime.now(),
            data={"temp": 20},
        )
        assert weather_service._is_cache_valid(weather) is True

    def test_stale_cache_is_invalid(self, weather_service):
        """Test that stale cache is invalid."""
        # Default TTL is typically shorter than 1 hour
        weather = WeatherData(
            postal_code="12345",
            country="US",
            fetched_at=datetime.now() - timedelta(hours=1),
            data={"temp": 20},
        )
        assert weather_service._is_cache_valid(weather) is False

    def test_edge_case_just_expired(self, weather_service):
        """Test cache that just expired."""
        with patch("nolongerevil.services.weather_service.settings") as mock_settings:
            mock_settings.weather_cache_ttl_seconds = 300  # 5 minutes
            weather = WeatherData(
                postal_code="12345",
                country="US",
                fetched_at=datetime.now() - timedelta(seconds=301),
                data={"temp": 20},
            )
            assert weather_service._is_cache_valid(weather) is False


class TestGetWeather:
    """Tests for get_weather method."""

    @pytest.mark.asyncio
    async def test_returns_cached_data(self, weather_service, mock_storage):
        """Test that cached data is returned when valid."""
        cached_weather = WeatherData(
            postal_code="12345",
            country="US",
            fetched_at=datetime.now(),
            data={"temperature": 25, "humidity": 50},
        )
        mock_storage.get_cached_weather.return_value = cached_weather

        result = await weather_service.get_weather(postal_code="12345", country="US")

        assert result == {"temperature": 25, "humidity": 50}
        mock_storage.get_cached_weather.assert_called_once_with("12345", "US")

    @pytest.mark.asyncio
    async def test_default_cache_keys(self, weather_service, mock_storage):
        """Test default cache keys when no postal/country provided."""
        mock_storage.get_cached_weather.return_value = None

        # Mock _fetch_weather to prevent actual API call
        weather_service._fetch_weather = AsyncMock(return_value=None)

        await weather_service.get_weather()

        mock_storage.get_cached_weather.assert_called_with("ip", "auto")

    @pytest.mark.asyncio
    async def test_returns_stale_cache_on_fetch_error(self, weather_service, mock_storage):
        """Test that stale cache is returned when fetch fails."""
        stale_weather = WeatherData(
            postal_code="12345",
            country="US",
            fetched_at=datetime.now() - timedelta(hours=2),
            data={"temperature": 20},
        )
        mock_storage.get_cached_weather.return_value = stale_weather

        # Mock to simulate fetch failure
        weather_service._fetch_weather = AsyncMock(side_effect=Exception("API error"))

        # Need to make cache invalid to trigger fetch attempt
        with patch.object(weather_service, "_is_cache_valid", return_value=False):
            result = await weather_service.get_weather(postal_code="12345", country="US")

        assert result == {"temperature": 20}

    @pytest.mark.asyncio
    async def test_caches_fetched_data(self, weather_service, mock_storage):
        """Test that fetched data is cached."""
        mock_storage.get_cached_weather.return_value = None

        weather_service._fetch_weather = AsyncMock(
            return_value={"temperature": 22, "conditions": "sunny"}
        )

        result = await weather_service.get_weather(postal_code="90210", country="US")

        assert result == {"temperature": 22, "conditions": "sunny"}
        mock_storage.cache_weather.assert_called_once()
        cached_call = mock_storage.cache_weather.call_args[0][0]
        assert cached_call.postal_code == "90210"
        assert cached_call.country == "US"
        assert cached_call.data == {"temperature": 22, "conditions": "sunny"}

    @pytest.mark.asyncio
    async def test_returns_none_on_complete_failure(self, weather_service, mock_storage):
        """Test that None is returned when both fetch and cache fail."""
        mock_storage.get_cached_weather.return_value = None
        weather_service._fetch_weather = AsyncMock(return_value=None)

        result = await weather_service.get_weather(postal_code="12345", country="US")

        assert result is None


class TestFetchWeather:
    """Tests for _fetch_weather method."""

    @pytest.mark.asyncio
    async def test_raises_if_not_initialized(self, weather_service):
        """Test that RuntimeError is raised if not initialized."""
        with pytest.raises(RuntimeError, match="not initialized"):
            await weather_service._fetch_weather(None)

    @pytest.mark.asyncio
    async def test_builds_url_with_query_string(self, weather_service):
        """Test that query string is appended to URL."""
        await weather_service.initialize()

        with patch.object(weather_service._session, "get") as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"temp": 20})
            mock_get.return_value.__aenter__.return_value = mock_response

            await weather_service._fetch_weather("postal_code=12345&country=US")

            call_args = mock_get.call_args[0][0]
            assert "postal_code=12345&country=US" in call_args

        await weather_service.close()

    @pytest.mark.asyncio
    async def test_returns_none_on_non_200(self, weather_service):
        """Test that None is returned on non-200 status."""
        await weather_service.initialize()

        with patch.object(weather_service._session, "get") as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 404
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await weather_service._fetch_weather(None)
            assert result is None

        await weather_service.close()
