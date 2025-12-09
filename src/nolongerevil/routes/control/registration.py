"""Control API registration endpoints - device registration and user management.

These endpoints allow the frontend to manage device registration without
direct database access, centralizing all DB operations in the Python backend.
"""

import re
from datetime import datetime

from aiohttp import web

from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import DeviceOwner, UserInfo
from nolongerevil.services.sqlite3_service import SQLite3Service

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

    storage: SQLite3Service = request.app["storage"]

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
        return web.json_response(
            {"success": False, "message": "Failed to claim entry key"}
        )

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

    storage: SQLite3Service = request.app["storage"]

    # Get all device ownership records for the user
    async with storage.db.execute(
        """
        SELECT serial, createdAt
        FROM deviceOwners
        WHERE userId = ?
        ORDER BY createdAt DESC
        """,
        (user_id,),
    ) as cursor:
        rows = await cursor.fetchall()

    devices = [{"serial": row["serial"], "createdAt": row["createdAt"]} for row in rows]

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

    storage: SQLite3Service = request.app["storage"]

    # Delete the ownership record
    cursor = await storage.db.execute(
        "DELETE FROM deviceOwners WHERE serial = ? AND userId = ?",
        (serial, user_id),
    )
    await storage.db.commit()

    if cursor.rowcount and cursor.rowcount > 0:
        logger.info(f"Deleted device {serial} for user {user_id}")
        return web.json_response(
            {"success": True, "message": f"Device {serial} deleted"}
        )
    else:
        logger.warning(f"Device {serial} not found for user {user_id}")
        return web.json_response(
            {"success": False, "message": f"Device {serial} not found"}
        )


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

    storage: SQLite3Service = request.app["storage"]

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

    storage: SQLite3Service = request.app["storage"]
    user_id = "homeassistant"

    # Check if integration exists
    async with storage.db.execute(
        "SELECT userId FROM integrations WHERE userId = ? AND type = ?",
        (user_id, "mqtt"),
    ) as cursor:
        existing = await cursor.fetchone()

    import json
    config_json = json.dumps(mqtt_config)
    now_ms = int(datetime.now().timestamp() * 1000)

    if existing:
        # Update existing
        await storage.db.execute(
            "UPDATE integrations SET enabled = ?, config = ?, updatedAt = ? WHERE userId = ? AND type = ?",
            (1, config_json, now_ms, user_id, "mqtt"),
        )
        await storage.db.commit()
        logger.info(f"Updated MQTT integration config for {user_id}")
        return web.json_response({"success": True, "created": False})
    else:
        # Insert new
        await storage.db.execute(
            "INSERT INTO integrations (userId, type, enabled, config, createdAt, updatedAt) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, "mqtt", 1, config_json, now_ms, now_ms),
        )
        await storage.db.commit()
        logger.info(f"Created MQTT integration config for {user_id}")
        return web.json_response({"success": True, "created": True})


def create_registration_routes(
    app: web.Application,
    storage: SQLite3Service,
) -> None:
    """Register registration routes.

    Args:
        app: aiohttp application
        storage: SQLite3 storage service
    """
    app["storage"] = storage

    app.router.add_post("/api/register", handle_register)
    app.router.add_get("/api/registered-devices", handle_registered_devices)
    app.router.add_delete("/api/registered-devices/{serial}", handle_delete_registered_device)
    app.router.add_post("/api/ensure-user", handle_ensure_user)
    app.router.add_post("/api/mqtt-config", handle_mqtt_config)
