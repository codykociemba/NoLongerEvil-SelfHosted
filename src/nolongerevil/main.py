"""Main application entry point with dual-port servers."""

import asyncio
import contextlib
import signal
import ssl
from datetime import datetime
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from nolongerevil.config import settings
from nolongerevil.integrations.integration_manager import IntegrationManager
from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import UserInfo
from nolongerevil.middleware.debug_logger import create_debug_logger_middleware
from nolongerevil.middleware.device_heartbeat import create_device_heartbeat_middleware
from nolongerevil.middleware.url_normalizer import create_url_normalizer_middleware
from nolongerevil.routes.control import get_control_routes
from nolongerevil.routes.nest import get_nest_routes
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
) -> Starlette:
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
        Starlette Application
    """
    # Build middleware list
    middleware: list[Middleware] = []

    # Add URL normalizer middleware
    url_normalizer_cls = create_url_normalizer_middleware()
    middleware.append(Middleware(url_normalizer_cls))

    # Add device heartbeat middleware
    heartbeat_cls = create_device_heartbeat_middleware(device_availability)
    middleware.append(Middleware(heartbeat_cls))

    # Add debug logger middleware if enabled
    debug_logger_cls = create_debug_logger_middleware()
    if debug_logger_cls:
        middleware.append(Middleware(debug_logger_cls))

    # Get Nest routes
    routes = get_nest_routes(
        state_service,
        subscription_manager,
        weather_service,
        device_availability,
    )

    app = Starlette(routes=routes, middleware=middleware)

    logger.info("Proxy (device) API application created")
    return app


def create_control_app(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
    device_availability: DeviceAvailability,
    storage: SQLModelService | None = None,
) -> Starlette:
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
        Starlette Application
    """
    # Build middleware list - CORS for control API
    middleware: list[Middleware] = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization", "X-API-Key"],
        ),
    ]

    # Add debug logger middleware if enabled
    debug_logger_cls = create_debug_logger_middleware()
    if debug_logger_cls:
        middleware.append(Middleware(debug_logger_cls))

    # Health check endpoint
    async def health_check(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # Get control routes and add health check
    routes = get_control_routes(
        state_service,
        subscription_manager,
        device_availability,
        storage,
    )
    routes.append(Route("/health", health_check, methods=["GET"]))

    app = Starlette(routes=routes, middleware=middleware)

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
        storage,
    )

    # Get SSL context
    ssl_context = get_ssl_context()

    # Configure uvicorn servers
    proxy_config = uvicorn.Config(
        proxy_app,
        host=settings.proxy_host,
        port=settings.proxy_port,
        ssl_keyfile=str(Path(settings.cert_dir) / "privkey.pem") if ssl_context and settings.cert_dir else None,
        ssl_certfile=str(Path(settings.cert_dir) / "fullchain.pem") if ssl_context and settings.cert_dir else None,
        log_level="warning",  # Reduce uvicorn logging noise
    )
    control_config = uvicorn.Config(
        control_app,
        host=settings.control_host,
        port=settings.control_port,
        log_level="warning",
    )

    proxy_server = uvicorn.Server(proxy_config)
    control_server = uvicorn.Server(control_config)

    # Track shutdown state
    shutdown_event = asyncio.Event()

    def signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()
        # Signal uvicorn to shutdown
        proxy_server.should_exit = True
        control_server.should_exit = True

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    logger.info(f"Proxy (device) API starting on {settings.proxy_host}:{settings.proxy_port}")
    logger.info(f"Control API starting on {settings.control_host}:{settings.control_port}")

    # Run both servers concurrently
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.gather(
            proxy_server.serve(),
            control_server.serve(),
        )

    # Graceful shutdown
    logger.info("Starting graceful shutdown...")

    await integration_manager.stop()
    await device_availability.stop()

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
