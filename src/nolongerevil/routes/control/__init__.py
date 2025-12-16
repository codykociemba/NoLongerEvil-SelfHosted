"""Control API routes module."""

from starlette.routing import Route

from nolongerevil.services.device_availability import DeviceAvailability
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.sqlmodel_service import SQLModelService
from nolongerevil.services.subscription_manager import SubscriptionManager

from .command import create_command_routes
from .registration import create_registration_routes
from .status import create_status_routes
from .webui import create_webui_routes


def get_control_routes(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
    device_availability: DeviceAvailability,
    storage: SQLModelService | None = None,
) -> list[Route]:
    """Get all Control API routes.

    Args:
        state_service: Device state service
        subscription_manager: Subscription manager
        device_availability: Device availability service
        storage: SQLModel storage service (optional, for registration routes)

    Returns:
        List of all Control routes
    """
    routes: list[Route] = []
    routes.extend(create_command_routes(state_service, subscription_manager))
    routes.extend(create_status_routes(state_service, subscription_manager, device_availability))
    routes.extend(create_webui_routes())

    if storage:
        routes.extend(create_registration_routes(storage))

    return routes


__all__ = [
    "get_control_routes",
    "create_command_routes",
    "create_registration_routes",
    "create_status_routes",
    "create_webui_routes",
]
