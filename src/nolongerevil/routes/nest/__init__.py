"""Nest API routes module."""

from starlette.routing import Route

from nolongerevil.services.device_availability import DeviceAvailability
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.subscription_manager import SubscriptionManager
from nolongerevil.services.weather_service import WeatherService

from .entry import create_entry_routes
from .passphrase import create_passphrase_routes
from .ping import create_ping_routes
from .pro_info import create_pro_info_routes
from .transport import create_transport_routes
from .upload import create_upload_routes
from .weather import create_weather_routes


def get_nest_routes(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
    weather_service: WeatherService,
    device_availability: DeviceAvailability,
) -> list[Route]:
    """Get all Nest API routes.

    Args:
        state_service: Device state service
        subscription_manager: Subscription manager
        weather_service: Weather service
        device_availability: Device availability service

    Returns:
        List of all Nest routes
    """
    routes: list[Route] = []
    routes.extend(create_entry_routes())
    routes.extend(create_ping_routes())
    routes.extend(create_passphrase_routes(state_service))
    routes.extend(create_pro_info_routes())
    routes.extend(create_transport_routes(state_service, subscription_manager, device_availability))
    routes.extend(create_upload_routes())
    routes.extend(create_weather_routes(weather_service))
    return routes


__all__ = [
    "get_nest_routes",
    "create_entry_routes",
    "create_passphrase_routes",
    "create_ping_routes",
    "create_pro_info_routes",
    "create_transport_routes",
    "create_upload_routes",
    "create_weather_routes",
]
