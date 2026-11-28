"""Nest weather endpoint - weather data with caching."""

from aiohttp import web

from nolongerevil.lib.logger import get_logger
from nolongerevil.services.weather_service import WeatherService

logger = get_logger(__name__)


async def handle_weather(request: web.Request) -> web.Response:
    """Handle weather data request.

    Proxies to weather.nest.com with caching to reduce API calls.

    Query parameters:
        postal_code: Postal/ZIP code
        country: Country code (e.g., "US")
        (or other parameters passed through to Nest API)

    Returns:
        JSON response with weather data
    """
    weather_service: WeatherService = request.app["weather_service"]

    # Extract query parameters
    postal_code = request.query.get("postal_code")
    country = request.query.get("country")
    legacy_query = request.query.get("query")
    if legacy_query:
        query_postal_code, separator, query_country = legacy_query.partition(",")
        if separator:
            query_postal_code = query_postal_code.strip()
            query_country = query_country.strip()
            postal_code = postal_code or query_postal_code
            country = country or query_country
    query_string = request.query_string

    # Get weather data (cached or fresh)
    weather_data = await weather_service.get_weather(
        postal_code=postal_code,
        country=country,
        query_string=query_string,
    )

    if weather_data:
        return web.json_response(weather_data)
    else:
        logger.warning("Weather service unavailable")
        return web.json_response(
            {"error": "Weather service unavailable"},
            status=502,
        )


def create_weather_routes(
    app: web.Application,
    weather_service: WeatherService,
) -> None:
    """Register weather routes.

    Args:
        app: aiohttp application
        weather_service: Weather service instance
    """
    app["weather_service"] = weather_service
    app.router.add_get("/nest/weather/v1", handle_weather)
    app.router.add_get("/nest/weather/{path:.*}", handle_weather)
