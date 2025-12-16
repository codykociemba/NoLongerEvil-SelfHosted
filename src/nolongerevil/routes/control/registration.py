"""Control API registration endpoints - device registration and user management."""

import re
from datetime import datetime

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import DeviceOwner, IntegrationConfig, UserInfo
from nolongerevil.services.sqlmodel_service import SQLModelService

logger = get_logger(__name__)

# Entry code format: 7 alphanumeric characters (e.g., "123ABCD")
ENTRY_CODE_PATTERN = re.compile(r"^[A-Z0-9]{7}$", re.IGNORECASE)


def create_registration_handlers(storage: SQLModelService):
    """Create registration handlers with injected storage."""

    async def handle_register(request: Request) -> JSONResponse:
        """Handle POST /api/register - claim entry key and register device."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"success": False, "message": "Invalid JSON"}, status_code=400)

        code = body.get("code")
        user_id = body.get("userId")

        if not code or not user_id:
            return JSONResponse(
                {"success": False, "message": "Missing required fields: code, userId"},
                status_code=400,
            )

        # Validate entry code format
        code = str(code).upper().strip()
        if not ENTRY_CODE_PATTERN.match(code):
            return JSONResponse(
                {
                    "success": False,
                    "message": "Invalid entry code format. Must be exactly 7 alphanumeric characters.",
                },
                status_code=400,
            )

        # Get the entry key to find the serial
        entry_key = await storage.get_entry_key(code)
        if not entry_key:
            logger.warning(f"Entry key not found: {code}")
            return JSONResponse(
                {"success": False, "message": "Invalid, expired, or already claimed entry key"}
            )

        # Check if expired
        if entry_key.expires_at < datetime.now():
            logger.warning(f"Entry key expired: {code}")
            return JSONResponse(
                {"success": False, "message": "Invalid, expired, or already claimed entry key"}
            )

        # Check if already claimed
        if entry_key.claimed_by:
            logger.warning(f"Entry key already claimed: {code}")
            return JSONResponse(
                {"success": False, "message": "Invalid, expired, or already claimed entry key"}
            )

        # Claim the entry key
        claimed = await storage.claim_entry_key(code, user_id)
        if not claimed:
            return JSONResponse({"success": False, "message": "Failed to claim entry key"})

        serial = entry_key.serial

        # Register device to user (create ownership record)
        existing_owner = await storage.get_device_owner(serial)
        if existing_owner:
            logger.warning(f"Device {serial} already registered to {existing_owner.user_id}")
        else:
            owner = DeviceOwner(serial=serial, user_id=user_id, created_at=datetime.now())
            await storage.set_device_owner(owner)
            logger.info(f"Registered device {serial} to user {user_id}")

        return JSONResponse(
            {
                "success": True,
                "serial": serial,
                "message": f"Device {serial} registered to {user_id}",
            }
        )

    async def handle_registered_devices(request: Request) -> JSONResponse:
        """Handle GET /api/registered-devices - get devices registered to a user."""
        user_id = request.query_params.get("userId", "homeassistant")

        # Get all device serials for the user
        device_serials = await storage.get_user_devices(user_id)

        # Get ownership details for each device
        devices: list[dict[str, str | int]] = []
        for serial in device_serials:
            owner = await storage.get_device_owner(serial)
            if owner:
                created_at_ms = int(owner.created_at.timestamp() * 1000)
                devices.append({"serial": serial, "createdAt": created_at_ms})

        # Sort by createdAt descending
        devices.sort(key=lambda d: int(d["createdAt"]), reverse=True)

        return JSONResponse(devices)

    async def handle_delete_registered_device(request: Request) -> JSONResponse:
        """Handle DELETE /api/registered-devices/{serial} - delete device registration."""
        serial = request.path_params.get("serial")
        if not serial:
            return JSONResponse(
                {"success": False, "message": "Missing device serial"},
                status_code=400,
            )

        # Validate serial format (alphanumeric, dashes, underscores)
        if not re.match(r"^[A-Za-z0-9_-]+$", serial):
            return JSONResponse(
                {"success": False, "message": "Invalid device serial format"},
                status_code=400,
            )

        user_id = request.query_params.get("userId", "homeassistant")

        # Delete the ownership record
        deleted = await storage.delete_device_owner(serial, user_id)

        if deleted:
            logger.info(f"Deleted device {serial} for user {user_id}")
            return JSONResponse({"success": True, "message": f"Device {serial} deleted"})
        else:
            logger.warning(f"Device {serial} not found for user {user_id}")
            return JSONResponse({"success": False, "message": f"Device {serial} not found"})

    async def handle_ensure_user(request: Request) -> JSONResponse:
        """Handle POST /api/ensure-user - ensure a user exists."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"success": False, "message": "Invalid JSON"}, status_code=400)

        user_id = body.get("userId")
        if not user_id:
            return JSONResponse(
                {"success": False, "message": "Missing required field: userId"},
                status_code=400,
            )

        email = body.get("email", f"{user_id}@local")

        # Check if user already exists
        existing_user = await storage.get_user(user_id)
        if existing_user:
            logger.debug(f"User {user_id} already exists")
            return JSONResponse({"success": True, "created": False})

        # Create the user
        user = UserInfo(clerk_id=user_id, email=email, created_at=datetime.now())
        await storage.create_user(user)
        logger.info(f"Created user {user_id}")

        return JSONResponse({"success": True, "created": True})

    async def handle_mqtt_config(request: Request) -> JSONResponse:
        """Handle POST /api/mqtt-config - configure MQTT integration."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"success": False, "message": "Invalid JSON"}, status_code=400)

        broker_url = body.get("brokerUrl")
        if not broker_url:
            return JSONResponse(
                {"success": False, "message": "Missing required field: brokerUrl"},
                status_code=400,
            )

        # Build MQTT config
        mqtt_config = {
            "brokerUrl": broker_url,
            "username": body.get("username"),
            "password": body.get("password"),
            "clientId": body.get("clientId", "nolongerevil-homeassistant"),
            "topicPrefix": body.get("topicPrefix", "nolongerevil"),
            "discoveryPrefix": body.get("discoveryPrefix", "homeassistant"),
            "publishRaw": body.get("publishRaw", True),
            "homeAssistantDiscovery": body.get("homeAssistantDiscovery", True),
        }

        # Remove None values
        mqtt_config = {k: v for k, v in mqtt_config.items() if v is not None}

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
            logger.info(f"Updated MQTT integration config for {user_id}")
            return JSONResponse({"success": True, "created": False})
        else:
            logger.info(f"Created MQTT integration config for {user_id}")
            return JSONResponse({"success": True, "created": True})

    return (
        handle_register,
        handle_registered_devices,
        handle_delete_registered_device,
        handle_ensure_user,
        handle_mqtt_config,
    )


def create_registration_routes(storage: SQLModelService) -> list[Route]:
    """Create registration routes.

    Args:
        storage: SQLModel storage service

    Returns:
        List of Starlette routes
    """
    (
        handle_register,
        handle_registered_devices,
        handle_delete_registered_device,
        handle_ensure_user,
        handle_mqtt_config,
    ) = create_registration_handlers(storage)

    return [
        Route("/api/register", handle_register, methods=["POST"]),
        Route("/api/registered-devices", handle_registered_devices, methods=["GET"]),
        Route(
            "/api/registered-devices/{serial}", handle_delete_registered_device, methods=["DELETE"]
        ),
        Route("/api/ensure-user", handle_ensure_user, methods=["POST"]),
        Route("/api/mqtt-config", handle_mqtt_config, methods=["POST"]),
    ]
