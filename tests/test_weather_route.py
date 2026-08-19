"""Tests for the Nest weather route."""

from unittest.mock import AsyncMock

from aiohttp.test_utils import make_mocked_request

from nolongerevil.routes.nest.weather import handle_weather


async def test_legacy_query_provides_trimmed_weather_cache_coordinates():
    """Test that Nest's query=postal,country format is parsed for caching."""
    weather_service = AsyncMock(return_value={"39440,US": {}})
    weather_service.get_weather = AsyncMock(return_value={"39440,US": {}})
    request = make_mocked_request(
        "GET", "/nest/weather/v1?query=39440,%20US", app={"weather_service": weather_service}
    )

    response = await handle_weather(request)

    assert response.status == 200
    weather_service.get_weather.assert_awaited_once_with(
        postal_code="39440",
        country="US",
        query_string="query=39440, US",
    )


async def test_explicit_weather_query_parameters_are_preserved():
    """Test that explicit postal_code/country parameters remain supported."""
    weather_service = AsyncMock()
    weather_service.get_weather = AsyncMock(return_value={"39440,US": {}})
    request = make_mocked_request(
        "GET",
        "/nest/weather/v1?postal_code=39440&country=US",
        app={"weather_service": weather_service},
    )

    await handle_weather(request)

    weather_service.get_weather.assert_awaited_once_with(
        postal_code="39440",
        country="US",
        query_string="postal_code=39440&country=US",
    )
