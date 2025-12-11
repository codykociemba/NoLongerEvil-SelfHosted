"""Main application entry point with dual-port servers."""

import asyncio
import signal
import ssl
from collections.abc import Awaitable, Callable
from pathlib import Path

from aiohttp import web

from nolongerevil.config import settings
from nolongerevil.integrations.integration_manager import IntegrationManager
from nolongerevil.lib.logger import get_logger
from nolongerevil.middleware.debug_logger import create_debug_logger_middleware
from nolongerevil.middleware.url_normalizer import create_url_normalizer_middleware
from nolongerevil.routes.control import setup_control_routes
from nolongerevil.routes.nest import setup_nest_routes
from nolongerevil.services.device_availability import DeviceAvailability
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.sqlmodel_service import SQLModelService
from nolongerevil.services.subscription_manager import SubscriptionManager
from nolongerevil.services.weather_service import WeatherService

logger = get_logger(__name__)


def create_proxy_app(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
    weather_service: WeatherService,
    device_availability: DeviceAvailability,
) -> web.Application:
    """Create the proxy (device) API application.

    This application handles Nest protocol communication:
    - Entry point discovery
    - Device state updates
    - Long-poll subscriptions
    - Weather proxy

    Args:
        state_service: Device state service
        subscription_manager: Subscription manager
        weather_service: Weather service
        device_availability: Device availability service

    Returns:
        aiohttp Application
    """
    app = web.Application(
        middlewares=[
            create_url_normalizer_middleware(),  # type: ignore[list-item] # Must be first - before body reading
            create_debug_logger_middleware(),  # type: ignore[list-item]
        ]
    )

    # Set up Nest routes
    setup_nest_routes(
        app,
        state_service,
        subscription_manager,
        weather_service,
        device_availability,
    )

    logger.info("Proxy (device) API application created")
    return app


def create_control_app(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
    device_availability: DeviceAvailability,
) -> web.Application:
    """Create the control API application.

    This application handles dashboard/automation communication:
    - Device commands
    - Status queries
    - Device listing

    Args:
        state_service: Device state service
        subscription_manager: Subscription manager
        device_availability: Device availability service

    Returns:
        aiohttp Application
    """

    # CORS middleware for control API
    @web.middleware
    async def cors_middleware(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        if request.method == "OPTIONS":
            response: web.StreamResponse = web.Response()
        else:
            response = await handler(request)

        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-API-Key"
        return response

    app = web.Application(
        middlewares=[
            create_debug_logger_middleware(),  # type: ignore[list-item]
            cors_middleware,
        ]
    )

    # Set up control routes
    setup_control_routes(
        app,
        state_service,
        subscription_manager,
        device_availability,
    )

    # Health check endpoint
    async def health_check(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app.router.add_get("/health", health_check)

    logger.info("Control API application created")
    return app


def get_ssl_context() -> ssl.SSLContext | None:
    """Get SSL context if certificates are configured.

    Returns:
        SSL context or None
    """
    if not settings.cert_dir:
        return None

    cert_dir = Path(settings.cert_dir)
    cert_file = cert_dir / "fullchain.pem"
    key_file = cert_dir / "privkey.pem"

    if not cert_file.exists() or not key_file.exists():
        logger.warning(f"SSL certificates not found in {cert_dir}")
        return None

    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_context.load_cert_chain(str(cert_file), str(key_file))
    logger.info(f"SSL context loaded from {cert_dir}")
    return ssl_context


async def run_server() -> None:
    """Run the dual-port server."""
    # Ensure data directory exists
    settings.ensure_data_dir()

    # Initialize storage with SQLModel
    logger.info("Initializing SQLModel storage backend")
    storage = SQLModelService()

    # Initialize services
    state_service = DeviceStateService(storage)
    await state_service.initialize()

    subscription_manager = SubscriptionManager()

    weather_service = WeatherService(storage)
    await weather_service.initialize()

    device_availability = DeviceAvailability(subscription_manager)

    # Initialize integration manager
    integration_manager = IntegrationManager(storage, state_service)
    state_service.set_integration_manager(integration_manager)
    device_availability.set_integration_manager(integration_manager)

    # Start background services
    await device_availability.start()
    await integration_manager.start()

    # Create applications
    proxy_app = create_proxy_app(
        state_service,
        subscription_manager,
        weather_service,
        device_availability,
    )
    control_app = create_control_app(
        state_service,
        subscription_manager,
        device_availability,
    )

    # Get SSL context
    ssl_context = get_ssl_context()

    # Create runners
    proxy_runner = web.AppRunner(proxy_app)
    control_runner = web.AppRunner(control_app)

    await proxy_runner.setup()
    await control_runner.setup()

    # Start servers
    proxy_site = web.TCPSite(
        proxy_runner,
        "0.0.0.0",
        settings.proxy_port,
        ssl_context=ssl_context,
    )
    control_site = web.TCPSite(
        control_runner,
        "0.0.0.0",
        settings.control_port,
    )

    await proxy_site.start()
    await control_site.start()

    logger.info(f"Proxy (device) API started on port {settings.proxy_port}")
    logger.info(f"Control API started on port {settings.control_port}")

    # Wait for shutdown signal
    shutdown_event = asyncio.Event()

    def signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    await shutdown_event.wait()

    # Graceful shutdown
    logger.info("Starting graceful shutdown...")

    await integration_manager.stop()
    await device_availability.stop()

    await proxy_runner.cleanup()
    await control_runner.cleanup()

    await weather_service.close()
    await state_service.close()

    logger.info("Server shutdown complete")


def main() -> None:
    """Main entry point."""
    logger.info("Starting NoLongerEvil server...")
    logger.info(f"API Origin: {settings.api_origin}")
    logger.info(f"Proxy Port: {settings.proxy_port}")
    logger.info(f"Control Port: {settings.control_port}")

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("Server interrupted")
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
