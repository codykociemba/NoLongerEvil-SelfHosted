"""Device heartbeat middleware - marks devices as seen on any request."""

from collections.abc import Awaitable, Callable

from aiohttp import web

from nolongerevil.lib.serial_parser import extract_serial_from_request
from nolongerevil.services.device_availability import DeviceAvailability

_MiddlewareType = Callable[
    [web.Request, Callable[[web.Request], Awaitable[web.StreamResponse]]],
    Awaitable[web.StreamResponse],
]


def create_device_heartbeat_middleware(
    device_availability: DeviceAvailability,
) -> _MiddlewareType:
    """Create middleware that marks devices as seen on any request.

    This middleware extracts the device serial from incoming requests
    and updates the device availability tracker, preventing false
    unavailability warnings when devices are actively communicating.

    Args:
        device_availability: Device availability service

    Returns:
        Middleware function
    """

    @web.middleware
    async def device_heartbeat_middleware(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        """Mark device as seen before processing request."""
        serial = extract_serial_from_request(request)

        if serial:
            await device_availability.mark_device_seen(serial)

        return await handler(request)

    return device_heartbeat_middleware
