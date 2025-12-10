"""Weather service with caching and proxying."""

from datetime import datetime, timedelta
from typing import Any

import aiohttp

from nolongerevil.config import settings
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import WeatherData
from nolongerevil.services.abstract_device_state_manager import AbstractDeviceStateManager

logger = get_logger(__name__)

NEST_WEATHER_URL = "https://weather.nest.com/weather/v1"


class WeatherService:
    """Weather service with caching.

    Proxies requests to weather.nest.com and caches responses
    to reduce API calls.
    """

    def __init__(self, storage: AbstractDeviceStateManager) -> None:
        """Initialize the weather service.

        Args:
            storage: Storage backend for caching
        """
        self._storage = storage
        self._session: aiohttp.ClientSession | None = None

    async def initialize(self) -> None:
        """Initialize the HTTP session.

        Note: SSL verification is disabled for weather.nest.com because it uses
        a private certificate authority (Nest Private Server Certificate Authority)
        that is not in public trust stores.
        """
        # Disable SSL verification for Nest's private CA
        connector = aiohttp.TCPConnector(ssl=False)
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=30),
        )
        logger.info("Weather service initialized (SSL verification disabled for Nest private CA)")

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None
            logger.info("Weather service closed")

    def _is_cache_valid(self, weather: WeatherData) -> bool:
        """Check if cached weather data is still valid.

        Args:
            weather: Cached weather data

        Returns:
            True if cache is valid
        """
        age = datetime.now() - weather.fetched_at
        return age < timedelta(seconds=settings.weather_cache_ttl_seconds)

    async def get_weather(
        self,
        postal_code: str | None = None,
        country: str | None = None,
        query_string: str | None = None,
    ) -> dict[str, Any] | None:
        """Get weather data, using cache if available.

        Args:
            postal_code: Postal/ZIP code
            country: Country code (e.g., "US")
            query_string: Raw query string from request

        Returns:
            Weather data dictionary or None on error
        """
        # Determine cache key
        cache_postal = postal_code or "ip"
        cache_country = country or "auto"

        # Check cache
        cached = await self._storage.get_cached_weather(cache_postal, cache_country)
        if cached and self._is_cache_valid(cached):
            logger.debug(f"Weather cache hit for {cache_postal}/{cache_country}")
            return cached.data

        # Fetch from Nest weather API
        logger.debug(f"Weather cache miss for {cache_postal}/{cache_country}, fetching...")

        try:
            data = await self._fetch_weather(query_string)
            if data:
                # Cache the result
                weather = WeatherData(
                    postal_code=cache_postal,
                    country=cache_country,
                    fetched_at=datetime.now(),
                    data=data,
                )
                await self._storage.cache_weather(weather)
                return data
        except Exception as e:
            logger.error(f"Failed to fetch weather: {e}")

        # Return stale cache if available
        if cached:
            logger.warning(f"Returning stale weather cache for {cache_postal}/{cache_country}")
            return cached.data

        return None

    async def _fetch_weather(self, query_string: str | None) -> dict[str, Any] | None:
        """Fetch weather data from Nest weather API.

        Args:
            query_string: Raw query string from original request

        Returns:
            Weather data dictionary or None on error
        """
        if not self._session:
            raise RuntimeError("Weather service not initialized")

        url = NEST_WEATHER_URL
        if query_string:
            url = f"{url}?{query_string}"

        logger.debug(f"Fetching weather from: {url}")

        try:
            async with self._session.get(url) as response:
                if response.status == 200:
                    result: dict[str, Any] = await response.json()
                    return result
                else:
                    logger.warning(f"Weather API returned status {response.status} for URL: {url}")
                    return None
        except aiohttp.ClientError as e:
            logger.error(f"Weather API request failed: {e}")
            return None
