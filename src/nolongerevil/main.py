"""Main application entry point with dual-port servers."""

import asyncio
import signal
import ssl
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from aiohttp import web

from nolongerevil.config import settings
from nolongerevil.integrations.integration_manager import IntegrationManager
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import UserInfo
from nolongerevil.middleware.debug_logger import create_debug_logger_middleware
from nolongerevil.middleware.device_auth import create_device_auth_middleware
from nolongerevil.middleware.device_heartbeat import create_device_heartbeat_middleware
from nolongerevil.middleware.url_normalizer import create_url_normalizer_middleware
from nolongerevil.routes.control import setup_control_routes
from nolongerevil.routes.nest import setup_nest_routes
from nolongerevil.services.device_availability import DeviceAvailability
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.sqlmodel_service import SQLModelService
from nolongerevil.services.subscription_manager import SubscriptionManager
from nolongerevil.services.weather_service import WeatherService

logger = get_logger(__name__)


async def ensure_homeassistant_user(storage: SQLModelService) -> None:
    """Ensure the homeassistant user exists in the database.

    Args:
        storage: SQLModel storage service
    """
    user_id = "homeassistant"
    existing_user = await storage.get_user(user_id)

    if existing_user:
        logger.debug(f"User '{user_id}' already exists")
        return

    user = UserInfo(
        clerk_id=user_id,
        email="homeassistant@local",
        created_at=datetime.now(),
    )
    await storage.create_user(user)
    logger.info(f"Created user '{user_id}'")


async def initialize_mqtt_config(storage: SQLModelService) -> None:
    """Initialize MQTT configuration from environment variables.

    Args:
        storage: SQLModel storage service
    """
    from nolongerevil.lib.types import IntegrationConfig

    if not settings.mqtt_host:
        logger.warning("MQTT not configured - no MQTT_HOST environment variable")
        return

    broker_url = settings.mqtt_broker_url
    logger.info(f"Initializing MQTT configuration: {broker_url}")

    mqtt_config = {
        "brokerUrl": broker_url,
        "clientId": "nolongerevil-homeassistant",
        "topicPrefix": settings.mqtt_topic_prefix,
        "discoveryPrefix": settings.mqtt_discovery_prefix,
        "publishRaw": True,
        "homeAssistantDiscovery": True,
    }

    # Add credentials if provided
    if settings.mqtt_user:
        mqtt_config["username"] = settings.mqtt_user
    if settings.mqtt_password:
        mqtt_config["password"] = settings.mqtt_password

    user_id = "homeassistant"

    # Check if integration exists
    existing_integrations = await storage.get_integrations(user_id)
    existing_mqtt = next((i for i in existing_integrations if i.type == "mqtt"), None)

    now = datetime.now()
    integration = IntegrationConfig(
        user_id=user_id,
        type="mqtt",
        enabled=True,
        config=mqtt_config,
        created_at=existing_mqtt.created_at if existing_mqtt else now,
        updated_at=now,
    )

    await storage.upsert_integration(integration)

    if existing_mqtt:
        logger.info("Updated MQTT integration config")
    else:
        logger.info("Created MQTT integration config")

    logger.info(f"MQTT configured: broker={broker_url}, prefix={settings.mqtt_topic_prefix}")


def create_proxy_app(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
    weather_service: WeatherService,
    device_availability: DeviceAvailability,
    storage: SQLModelService,
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
        storage: SQLModel storage service for device owner lookups

    Returns:
        aiohttp Application
    """
    app = web.Application(
        middlewares=[
            create_url_normalizer_middleware(),  # type: ignore[list-item] # Must be first - before body reading
            create_device_auth_middleware(),  # type: ignore[list-item] # Auth before heartbeat — reject unknown devices early
            create_device_heartbeat_middleware(device_availability),  # type: ignore[list-item]
            create_debug_logger_middleware(),  # type: ignore[list-item]
        ]
    )

    # Store storage on app for device auth middleware and transport handler
    app["storage"] = storage

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
    storage: SQLModelService | None = None,
) -> web.Application:
    """Create the control API application.

    This application handles dashboard/automation communication:
    - Device commands
    - Status queries
    - Device listing
    - Device registration (when storage is provided)

    Args:
        state_service: Device state service
        subscription_manager: Subscription manager
        device_availability: Device availability service
        storage: SQLModel storage service (optional, for registration routes)

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
        storage,
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
    await storage.initialize()

    # Initialize user and MQTT configuration
    await ensure_homeassistant_user(storage)
    await initialize_mqtt_config(storage)

    # Initialize services
    state_service = DeviceStateService(storage)
    await state_service.initialize()

    subscription_manager = SubscriptionManager()

    weather_service = WeatherService(storage)
    await weather_service.initialize()

    device_availability = DeviceAvailability(subscription_manager)

    # Initialize availability tracking for devices loaded from storage
    known_serials = state_service.get_all_serials()
    device_availability.initialize_from_serials(known_serials)

    # Initialize integration manager
    integration_manager = IntegrationManager(storage, state_service, subscription_manager)
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
        storage,
    )
    control_app = create_control_app(
        state_service,
        subscription_manager,
        device_availability,
        storage,
    )
    control_app["integration_manager"] = integration_manager

    # Get SSL context
    ssl_context = get_ssl_context()

    # aiohttp keepalive_timeout must exceed connection_hold_timeout so the HTTP
    # server doesn't close idle connections before our hold loop finishes.
    keepalive_timeout = int(settings.connection_hold_timeout) + 60
    proxy_runner = web.AppRunner(proxy_app, keepalive_timeout=keepalive_timeout)
    control_runner = web.AppRunner(control_app)

    await proxy_runner.setup()
    await control_runner.setup()

    # Start servers
    proxy_site = web.TCPSite(
        proxy_runner,
        settings.server_host,
        settings.server_port,
        ssl_context=ssl_context,
    )
    control_site = web.TCPSite(
        control_runner,
        settings.control_host,
        settings.control_port,
    )

    await proxy_site.start()
    await control_site.start()

    logger.info(f"Server started on {settings.server_host}:{settings.server_port}")
    logger.info(f"Control API started on {settings.control_host}:{settings.control_port}")

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
    logger.info(f"Server Port: {settings.server_port}")
    logger.info(f"Control Port: {settings.control_port}")
    logger.info(
        f"Timing: suspend_time_max={settings.suspend_time_max}s (device sleep), "
        f"connection_hold={settings.connection_hold_timeout}s (server hold), "
        f"defer_device_window={settings.defer_device_window}s"
    )

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("Server interrupted")
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
