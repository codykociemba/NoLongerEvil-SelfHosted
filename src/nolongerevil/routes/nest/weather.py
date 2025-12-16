"""Nest weather endpoint - weather data with caching."""

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from nolongerevil.lib.logger import get_logger
from nolongerevil.services.weather_service import WeatherService

logger = get_logger(__name__)


def create_weather_handler(weather_service: WeatherService):
    """Create weather handler with injected service."""

    async def handle_weather(request: Request) -> JSONResponse:
        """Handle weather data request.

        Proxies to weather.nest.com with caching to reduce API calls.

        Query parameters:
            postal_code: Postal/ZIP code
            country: Country code (e.g., "US")
            (or other parameters passed through to Nest API)

        Returns:
            JSON response with weather data
        """
        # Extract query parameters
        postal_code = request.query_params.get("postal_code")
        country = request.query_params.get("country")
        query_string = str(request.query_params)

        # Get weather data (cached or fresh)
        weather_data = await weather_service.get_weather(
            postal_code=postal_code,
            country=country,
            query_string=query_string,
        )

        if weather_data:
            return JSONResponse(weather_data)
        else:
            logger.warning("Weather service unavailable")
            return JSONResponse(
                {"error": "Weather service unavailable"},
                status_code=502,
            )

    return handle_weather


def create_weather_routes(weather_service: WeatherService) -> list[Route]:
    """Create weather routes.

    Args:
        weather_service: Weather service instance

    Returns:
        List of Starlette routes
    """
    handler = create_weather_handler(weather_service)
    return [
        Route("/nest/weather/v1", handler, methods=["GET"]),
        Route("/nest/weather/{path:path}", handler, methods=["GET"]),
    ]
