"""Main application entry point with ASGI server."""

import ssl
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

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


# Global state for services (initialized during lifespan)
_app_state: dict[str, Any] = {}


async def ensure_homeassistant_user(storage: SQLModelService) -> None:
    """Ensure the homeassistant user exists in the database."""
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
    """Initialize MQTT configuration from environment variables."""
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

    if settings.mqtt_user:
        mqtt_config["username"] = settings.mqtt_user
    if settings.mqtt_password:
        mqtt_config["password"] = settings.mqtt_password

    user_id = "homeassistant"

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


@asynccontextmanager
async def lifespan(app: Starlette):
    """Application lifespan context manager for startup/shutdown."""
    global _app_state

    # Startup
    logger.info("Starting NoLongerEvil server...")
    settings.ensure_data_dir()

    # Initialize storage
    logger.info("Initializing SQLModel storage backend")
    storage = SQLModelService()
    await storage.initialize()

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

    # Store in app state for routes
    _app_state = {
        "storage": storage,
        "state_service": state_service,
        "subscription_manager": subscription_manager,
        "weather_service": weather_service,
        "device_availability": device_availability,
        "integration_manager": integration_manager,
    }

    logger.info(f"Server ready on {settings.host}:{settings.port}")

    yield

    # Shutdown
    logger.info("Starting graceful shutdown...")

    await integration_manager.stop()
    await device_availability.stop()
    await weather_service.close()
    await state_service.close()

    logger.info("Server shutdown complete")


def create_app() -> Starlette:
    """Create the ASGI application.

    This is the application factory used by Gunicorn/uvicorn.

    Returns:
        Starlette application
    """
    # Build middleware list
    middleware: list[Middleware] = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization", "X-API-Key"],
        ),
    ]

    # Add URL normalizer middleware
    url_normalizer_cls = create_url_normalizer_middleware()
    middleware.append(Middleware(url_normalizer_cls))

    # Add debug logger middleware if enabled
    debug_logger_cls = create_debug_logger_middleware()
    if debug_logger_cls:
        middleware.append(Middleware(debug_logger_cls))

    # Health check endpoint
    async def health_check(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # Create placeholder services for route creation
    # Actual services are initialized during lifespan
    storage = SQLModelService()
    state_service = DeviceStateService(storage)
    subscription_manager = SubscriptionManager()
    weather_service = WeatherService(storage)
    device_availability = DeviceAvailability(subscription_manager)

    # Get all routes
    nest_routes = get_nest_routes(
        state_service,
        subscription_manager,
        weather_service,
        device_availability,
    )
    control_routes = get_control_routes(
        state_service,
        subscription_manager,
        device_availability,
        storage,
    )

    # Combine routes
    routes = nest_routes + control_routes + [Route("/health", health_check, methods=["GET"])]

    app = Starlette(
        routes=routes,
        middleware=middleware,
        lifespan=lifespan,
    )

    logger.info("Application created")
    return app


def get_ssl_context() -> ssl.SSLContext | None:
    """Get SSL context if certificates are configured."""
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


def main() -> None:
    """Main entry point for direct execution (development)."""
    import uvicorn

    logger.info(f"API Origin: {settings.api_origin}")
    logger.info(f"Server Port: {settings.port}")

    ssl_context = get_ssl_context()

    try:
        uvicorn.run(
            "nolongerevil.main:app",
            host=settings.host,
            port=settings.port,
            ssl_keyfile=str(Path(settings.cert_dir) / "privkey.pem")
            if ssl_context and settings.cert_dir
            else None,
            ssl_certfile=str(Path(settings.cert_dir) / "fullchain.pem")
            if ssl_context and settings.cert_dir
            else None,
            log_level="info",
            reload=False,
        )
    except KeyboardInterrupt:
        logger.info("Server interrupted")
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        raise


# App factory for ASGI servers
# Usage: gunicorn "nolongerevil.main:create_app()" -k uvicorn.workers.UvicornWorker
# Or: uvicorn nolongerevil.main:app --factory

if __name__ == "__main__":
    main()

# Module-level app instance for uvicorn
app = create_app()
