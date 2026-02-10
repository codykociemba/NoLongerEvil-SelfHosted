"""Control API registration endpoints - device registration and user management.

These endpoints allow the frontend to manage device registration without
direct database access, centralizing all DB operations in the Python backend.
"""

import re
import time
from datetime import datetime

from aiohttp import web

from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import DeviceObject, DeviceOwner, IntegrationConfig, UserInfo
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.sqlmodel_service import SQLModelService
from nolongerevil.services.subscription_manager import SubscriptionManager
from nolongerevil.utils.structure_assignment import derive_structure_id

logger = get_logger(__name__)

# Entry code format: 7 alphanumeric characters (e.g., "123ABCD")
ENTRY_CODE_PATTERN = re.compile(r"^[A-Z0-9]{7}$", re.IGNORECASE)


async def handle_register(request: web.Request) -> web.Response:
    """Handle POST /api/register - claim entry key and register device.

    Request body:
        {
            "code": "ABC1234",  # 7-character entry code
            "userId": "homeassistant"  # User ID to register to
        }

    Returns:
        JSON response with registration result:
        - Success: {"success": true, "serial": "...", "message": "..."}
        - Failure: {"success": false, "message": "..."}
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"success": False, "message": "Invalid JSON"},
            status=400,
        )

    code = body.get("code")
    user_id = body.get("userId")

    if not code or not user_id:
        return web.json_response(
            {"success": False, "message": "Missing required fields: code, userId"},
            status=400,
        )

    # Validate entry code format
    code = str(code).upper().strip()
    if not ENTRY_CODE_PATTERN.match(code):
        return web.json_response(
            {
                "success": False,
                "message": "Invalid entry code format. Must be exactly 7 alphanumeric characters.",
            },
            status=400,
        )

    storage: SQLModelService = request.app["storage"]

    # Get the entry key to find the serial
    entry_key = await storage.get_entry_key(code)
    if not entry_key:
        logger.warning(f"Entry key not found: {code}")
        return web.json_response(
            {"success": False, "message": "Invalid, expired, or already claimed entry key"}
        )

    # Check if expired
    if entry_key.expires_at < datetime.now():
        logger.warning(f"Entry key expired: {code}")
        return web.json_response(
            {"success": False, "message": "Invalid, expired, or already claimed entry key"}
        )

    # Check if already claimed
    if entry_key.claimed_by:
        logger.warning(f"Entry key already claimed: {code}")
        return web.json_response(
            {"success": False, "message": "Invalid, expired, or already claimed entry key"}
        )

    # Claim the entry key
    claimed = await storage.claim_entry_key(code, user_id)
    if not claimed:
        return web.json_response({"success": False, "message": "Failed to claim entry key"})

    serial = entry_key.serial

    # Register device to user (create ownership record)
    existing_owner = await storage.get_device_owner(serial)
    if existing_owner:
        logger.warning(f"Device {serial} already registered to {existing_owner.user_id}")
    else:
        owner = DeviceOwner(
            serial=serial,
            user_id=user_id,
            created_at=datetime.now(),
        )
        await storage.set_device_owner(owner)
        logger.info(f"Registered device {serial} to user {user_id}")

    # Push user bucket + structure bucket to complete pairing on the device.
    # The user bucket's "name" field is what triggers pairing completion.
    # The structure bucket alone is not sufficient — the device requires
    # the user bucket to be present first.
    state_service: DeviceStateService | None = request.app.get("state_service")
    subscription_manager: SubscriptionManager | None = request.app.get("subscription_manager")

    if state_service and subscription_manager:
        now_ts = int(time.time() * 1000)
        objects_to_push: list[DeviceObject] = []

        # User bucket — triggers pairing completion
        user_key = f"user.{user_id}"
        existing_user = state_service.get_object(serial, user_key)
        if existing_user:
            user_rev = existing_user.object_revision + 1
            user_value = {**existing_user.value, "name": user_id}
        else:
            user_rev = 1
            user_value = {"name": user_id}

        user_obj = DeviceObject(
            serial=serial,
            object_key=user_key,
            object_revision=user_rev,
            object_timestamp=now_ts,
            value=user_value,
            updated_at=datetime.now(),
        )
        await state_service.upsert_object(user_obj)
        objects_to_push.append(user_obj)
        logger.info(f"Created/updated user bucket {user_key} for {serial}")

        # Structure bucket — establishes device-home association
        structure_id = derive_structure_id(user_id)
        structure_key = f"structure.{structure_id}"
        existing_structure = state_service.get_object(serial, structure_key)
        if existing_structure:
            new_rev = existing_structure.object_revision + 1
            structure_value = {
                **existing_structure.value,
                "devices": list(set(existing_structure.value.get("devices", []) + [serial])),
            }
        else:
            new_rev = 1
            structure_value = {
                "name": "Home",
                "devices": [serial],
            }

        structure_obj = DeviceObject(
            serial=serial,
            object_key=structure_key,
            object_revision=new_rev,
            object_timestamp=now_ts,
            value=structure_value,
            updated_at=datetime.now(),
        )
        await state_service.upsert_object(structure_obj)
        objects_to_push.append(structure_obj)
        logger.info(f"Created/updated structure bucket {structure_key} for {serial}")

        # Push both to any held subscribe connections
        notified = await subscription_manager.notify_all_subscribers(serial, objects_to_push)
        if notified:
            logger.info(f"Pushed user + structure buckets to {notified} subscriber(s) for {serial}")
        else:
            logger.info(
                f"No active subscribers for {serial} - buckets will be included on next subscribe"
            )
    else:
        logger.warning("state_service or subscription_manager not available for pairing completion")

    return web.json_response(
        {
            "success": True,
            "serial": serial,
            "message": f"Device {serial} registered to {user_id}",
        }
    )


async def handle_registered_devices(request: web.Request) -> web.Response:
    """Handle GET /api/registered-devices - get devices registered to a user.

    Query parameters:
        userId: User ID to get devices for (default: "homeassistant")

    Returns:
        JSON array of registered devices:
        [{"serial": "...", "createdAt": 1234567890}, ...]
    """
    user_id = request.query.get("userId", "homeassistant")

    storage: SQLModelService = request.app["storage"]

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

    return web.json_response(devices)


async def handle_delete_registered_device(request: web.Request) -> web.Response:
    """Handle DELETE /api/registered-devices/{serial} - delete device registration.

    Path parameters:
        serial: Device serial to delete

    Query parameters:
        userId: User ID (default: "homeassistant")

    Returns:
        JSON response with deletion result:
        - Success: {"success": true, "message": "..."}
        - Not found: {"success": false, "message": "..."}
    """
    serial = request.match_info.get("serial")
    if not serial:
        return web.json_response(
            {"success": False, "message": "Missing device serial"},
            status=400,
        )

    # Validate serial format (alphanumeric, dashes, underscores)
    if not re.match(r"^[A-Za-z0-9_-]+$", serial):
        return web.json_response(
            {"success": False, "message": "Invalid device serial format"},
            status=400,
        )

    user_id = request.query.get("userId", "homeassistant")

    storage: SQLModelService = request.app["storage"]

    # Delete the ownership record
    deleted = await storage.delete_device_owner(serial, user_id)

    if deleted:
        logger.info(f"Deleted device {serial} for user {user_id}")
        return web.json_response({"success": True, "message": f"Device {serial} deleted"})
    else:
        logger.warning(f"Device {serial} not found for user {user_id}")
        return web.json_response({"success": False, "message": f"Device {serial} not found"})


async def handle_ensure_user(request: web.Request) -> web.Response:
    """Handle POST /api/ensure-user - ensure a user exists.

    Request body:
        {
            "userId": "homeassistant",
            "email": "homeassistant@local"  # Optional
        }

    Returns:
        JSON response:
        - {"success": true, "created": true}  # User was created
        - {"success": true, "created": false}  # User already existed
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"success": False, "message": "Invalid JSON"},
            status=400,
        )

    user_id = body.get("userId")
    if not user_id:
        return web.json_response(
            {"success": False, "message": "Missing required field: userId"},
            status=400,
        )

    email = body.get("email", f"{user_id}@local")

    storage: SQLModelService = request.app["storage"]

    # Check if user already exists
    existing_user = await storage.get_user(user_id)
    if existing_user:
        logger.debug(f"User {user_id} already exists")
        return web.json_response({"success": True, "created": False})

    # Create the user
    user = UserInfo(
        clerk_id=user_id,
        email=email,
        created_at=datetime.now(),
    )
    await storage.create_user(user)
    logger.info(f"Created user {user_id}")

    return web.json_response({"success": True, "created": True})


async def handle_mqtt_config(request: web.Request) -> web.Response:
    """Handle POST /api/mqtt-config - configure MQTT integration.

    Request body:
        {
            "brokerUrl": "mqtt://host:port",
            "username": "user",  # Optional
            "password": "pass",  # Optional
            "topicPrefix": "nolongerevil",
            "discoveryPrefix": "homeassistant",
            "homeAssistantDiscovery": true
        }

    Returns:
        JSON response:
        - {"success": true, "created": true}  # Config was created
        - {"success": true, "created": false}  # Config was updated
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"success": False, "message": "Invalid JSON"},
            status=400,
        )

    broker_url = body.get("brokerUrl")
    if not broker_url:
        return web.json_response(
            {"success": False, "message": "Missing required field: brokerUrl"},
            status=400,
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

    storage: SQLModelService = request.app["storage"]
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
        return web.json_response({"success": True, "created": False})
    else:
        logger.info(f"Created MQTT integration config for {user_id}")
        return web.json_response({"success": True, "created": True})


def create_registration_routes(
    app: web.Application,
    storage: SQLModelService,
    state_service: DeviceStateService | None = None,
    subscription_manager: SubscriptionManager | None = None,
) -> None:
    """Register registration routes.

    Args:
        app: aiohttp application
        storage: SQLModel storage service
        state_service: Device state service (for dismissing pairing dialog)
        subscription_manager: Subscription manager (for notifying device)
    """
    app["storage"] = storage
    if state_service:
        app["state_service"] = state_service
    if subscription_manager:
        app["subscription_manager"] = subscription_manager

    app.router.add_post("/api/register", handle_register)
    app.router.add_get("/api/registered-devices", handle_registered_devices)
    app.router.add_delete("/api/registered-devices/{serial}", handle_delete_registered_device)
    app.router.add_post("/api/ensure-user", handle_ensure_user)
    app.router.add_post("/api/mqtt-config", handle_mqtt_config)
