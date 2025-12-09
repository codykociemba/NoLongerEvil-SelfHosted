"""Nest transport endpoint - device state management and subscriptions."""

import asyncio
import json
import time
from datetime import datetime
from typing import Any

from aiohttp import web

from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.serial_parser import extract_serial_from_request, extract_weave_device_id
from nolongerevil.lib.types import DeviceObject
from nolongerevil.services.device_availability import DeviceAvailability
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.subscription_manager import SubscriptionManager
from nolongerevil.utils.fan_timer import preserve_fan_timer_state
from nolongerevil.utils.structure_assignment import assign_structure_id

logger = get_logger(__name__)


def parse_object_key(object_key: str) -> tuple[str, str]:
    """Parse an object key into type and serial."""
    parts = object_key.split(".", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return object_key, ""


def format_object_for_response(obj: DeviceObject, include_value: bool = True) -> dict[str, Any]:
    """Format a device object for JSON response."""
    result: dict[str, Any] = {
        "serial": obj.serial,
        "object_key": obj.object_key,
        "object_revision": obj.object_revision,
        "object_timestamp": obj.object_timestamp,
    }
    if obj.updated_at:
        result["updatedAt"] = int(obj.updated_at.timestamp() * 1000)
    if include_value:
        result["value"] = obj.value
    return result


async def handle_transport_get(request: web.Request) -> web.Response:
    """Handle GET /nest/transport/device/{serial} - list device objects.

    Also handles legacy paths like /nest/transport/v7/device/device.{serial}
    """
    # Try to get serial from match_info first
    serial = request.match_info.get("serial")

    # If not found, try to extract from the path (legacy paths)
    if not serial:
        # Check for pattern like /device/device.{serial} in path
        path = request.path
        if "/device/" in path:
            # Extract everything after /device/
            device_part = path.split("/device/")[-1]
            # Remove "device." prefix if present
            serial = device_part[7:] if device_part.startswith("device.") else device_part

    # Also try extracting from request headers/body
    if not serial:
        serial = extract_serial_from_request(request)

    if not serial:
        return web.json_response({"error": "Serial required"}, status=400)

    state_service: DeviceStateService = request.app["state_service"]

    # Ensure device alert dialog exists (matches TypeScript behavior)
    await state_service.storage.ensure_device_alert_dialog(serial)

    objects = state_service.get_objects_by_serial(serial)

    # Return only metadata, not values
    response_objects = [
        {
            "object_revision": obj.object_revision,
            "object_timestamp": obj.object_timestamp,
            "object_key": obj.object_key,
        }
        for obj in objects
    ]

    return web.json_response({"objects": response_objects})


async def handle_transport_subscribe(request: web.Request) -> web.StreamResponse:
    """Handle POST /nest/transport - subscribe to device updates.

    This is the main Nest protocol endpoint. It handles:
    1. Device sending state updates (when value provided with rev/ts = 0)
    2. Device subscribing to updates (long-poll)
    3. Server responding with outdated objects if device is behind
    """
    serial = extract_serial_from_request(request)
    if not serial:
        return web.json_response({"error": "Device serial required"}, status=400)

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    session = body.get("session", f"session_{serial}_{int(time.time() * 1000)}")
    chunked = body.get("chunked", False)
    objects = body.get("objects", [])
    weave_device_id = extract_weave_device_id(request)

    logger.debug(
        f"Subscribe from {serial}: chunked={chunked}, {len(objects)} objects, session={session}"
    )
    for obj in objects:  # Log all objects
        logger.debug(
            f"  Object: key={obj.get('object_key')} rev={obj.get('object_revision')} ts={obj.get('object_timestamp')}"
        )

    if not isinstance(objects, list):
        return web.Response(text="Invalid request: objects array required", status=400)

    state_service: DeviceStateService = request.app["state_service"]
    subscription_manager: SubscriptionManager = request.app["subscription_manager"]
    device_availability: DeviceAvailability = request.app["device_availability"]

    # Mark device as seen
    await device_availability.mark_device_seen(serial)

    response_objects: list[DeviceObject] = []
    # Track which client objects we processed (those with valid object_key)
    processed_client_objects: list[dict[str, Any]] = []

    # Process each object from the device
    for client_obj in objects:
        object_key = client_obj.get("object_key")
        if not object_key:
            continue

        processed_client_objects.append(client_obj)
        object_revision = client_obj.get("object_revision", 0)
        object_timestamp = client_obj.get("object_timestamp", 0)
        value = client_obj.get("value")

        # Get current server state
        server_obj = state_service.get_object(serial, object_key)

        # Check if this is an UPDATE from the device
        # (has value, and revision/timestamp are 0 or not provided)
        is_update = (
            value is not None
            and (object_revision == 0 or object_revision is None)
            and (object_timestamp == 0 or object_timestamp is None)
        )

        if is_update:
            # Device is sending us an update
            existing_value = server_obj.value if server_obj else {}
            merged_value = {**existing_value, **value}

            # Store weave_device_id if provided
            if weave_device_id:
                merged_value["weave_device_id"] = weave_device_id

            # Apply object-type specific logic
            object_type, _ = parse_object_key(object_key)
            if object_type == "device":
                # Preserve fan timer state for device objects
                merged_value = preserve_fan_timer_state(existing_value, merged_value, serial)

                # Auto-assign structure_id based on device owner
                device_owner = await state_service.storage.get_device_owner(serial)
                if device_owner:
                    merged_value = assign_structure_id(merged_value, device_owner.user_id, serial)

                    # Sync user state when away or postal_code changes
                    if "away" in value or "postal_code" in value:
                        await state_service.storage.update_user_away_status(device_owner.user_id)
                        await state_service.storage.sync_user_weather_from_device(
                            device_owner.user_id
                        )

            # Check if values actually changed
            values_equal = server_obj and _values_equal(server_obj.value, merged_value)
            new_revision = (
                (server_obj.object_revision if server_obj else 0)
                if values_equal
                else (server_obj.object_revision if server_obj else 0) + 1
            )
            new_timestamp = int(time.time() * 1000)

            # Save the update
            server_obj = await state_service.upsert_object(
                DeviceObject(
                    serial=serial,
                    object_key=object_key,
                    object_revision=new_revision,
                    object_timestamp=new_timestamp,
                    value=merged_value,
                    updated_at=datetime.utcnow(),
                )
            )

        # Build response object
        if server_obj:
            response_objects.append(server_obj)
        else:
            # No server state yet - create placeholder
            response_objects.append(
                DeviceObject(
                    serial=serial,
                    object_key=object_key,
                    object_revision=0,
                    object_timestamp=0,
                    value={},
                    updated_at=datetime.utcnow(),
                )
            )

    # Find outdated objects (server has newer data than client)
    outdated_objects: list[DeviceObject] = []
    objects_to_merge: list[tuple[dict[str, Any], DeviceObject]] = []

    for i, client_obj in enumerate(processed_client_objects):
        response_obj = response_objects[i]
        client_revision = client_obj.get("object_revision", 0)
        client_timestamp = client_obj.get("object_timestamp", 0)

        # If client sent rev=0 and ts=0, they want our full state
        if client_revision == 0 and client_timestamp == 0:
            outdated_objects.append(response_obj)
            continue

        # Check if server has newer data
        # Prioritize revision comparison - timestamps can be inconsistent (seconds vs ms)
        server_revision_higher = response_obj.object_revision > client_revision
        client_revision_higher = client_revision > response_obj.object_revision

        if server_revision_higher:
            # Server has newer revision - send our data to device
            outdated_objects.append(response_obj)
        elif client_revision_higher:
            # Client has newer revision - merge their data
            objects_to_merge.append((client_obj, response_obj))
        # If revisions are equal, check timestamps (but be lenient due to potential scale differences)

    # Merge client updates that are newer than server
    for client_obj, server_obj in objects_to_merge:
        object_key = client_obj.get("object_key")
        client_value = client_obj.get("value")
        if client_value and object_key:
            merged_value = {**server_obj.value, **client_value}
            await state_service.upsert_object(
                DeviceObject(
                    serial=serial,
                    object_key=object_key,
                    object_revision=client_obj.get("object_revision", 0),
                    object_timestamp=client_obj.get("object_timestamp", 0),
                    value=merged_value,
                    updated_at=datetime.utcnow(),
                )
            )

    # If there are outdated objects, respond immediately
    if outdated_objects:
        formatted_objs = [format_object_for_response(obj) for obj in outdated_objects]
        logger.debug(
            f"Responding immediately with {len(outdated_objects)} outdated object(s) for {serial}"
        )
        for obj in formatted_objs[:3]:
            logger.debug(
                f"  Response: key={obj.get('object_key')} rev={obj.get('object_revision')} ts={obj.get('object_timestamp')}"
            )
        response_data = {"objects": formatted_objs}
        return web.json_response(
            response_data,
            headers={"X-nl-service-timestamp": str(int(time.time() * 1000))},
        )

    # No immediate updates - handle subscription
    if chunked:
        # Chunked mode - keep connection open
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "Transfer-Encoding": "chunked",
                "X-nl-service-timestamp": str(int(time.time() * 1000)),
            },
        )
        await response.prepare(request)

        # Build subscribed keys map
        subscribed_keys = {
            obj.get("object_key"): obj.get("object_revision", 0)
            for obj in objects
            if obj.get("object_key")
        }

        # Add to subscription manager
        added = await subscription_manager.add_chunked_subscription(
            serial, session, subscribed_keys, response
        )

        if not added:
            await response.write(b'{"error": "Too many subscriptions"}\r\n')
            await response.write_eof()
            return response

        logger.debug(f"Added chunked subscription for {serial} (session: {session})")

        # Write an empty chunk to initialize the stream
        await response.write(b"")

        # Wait for the response to be closed (either by client disconnect or server write_eof)
        # The subscription manager will write and close when updates arrive
        try:
            # Wait indefinitely - the connection will be closed by:
            # 1. Client disconnect (CancelledError)
            # 2. Server notification (write_eof called by subscription manager)
            while True:
                # Check if the response has been ended
                if response.prepared and response._payload_writer is None:
                    break
                await asyncio.sleep(1)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            await subscription_manager.remove_chunked_subscription(serial, session)

        return response
    else:
        # Non-chunked mode - just close
        return web.json_response(
            {"objects": []},
            headers={"Content-Type": "application/json"},
        )


async def handle_transport_put(request: web.Request) -> web.Response:
    """Handle POST /nest/transport/put - device state updates."""
    serial = extract_serial_from_request(request)
    if not serial:
        return web.json_response({"error": "Device serial required"}, status=400)

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    objects = body.get("objects", [])
    if not isinstance(objects, list):
        return web.Response(text="Invalid request: objects array required", status=400)

    state_service: DeviceStateService = request.app["state_service"]
    subscription_manager: SubscriptionManager = request.app["subscription_manager"]
    device_availability: DeviceAvailability = request.app["device_availability"]

    # Mark device as seen
    await device_availability.mark_device_seen(serial)

    weave_device_id = extract_weave_device_id(request)
    response_objects: list[dict[str, Any]] = []
    device_object_changed = False

    for client_obj in objects:
        object_key = client_obj.get("object_key")
        value = client_obj.get("value")

        if not object_key or not value:
            logger.warning(f"No value provided for {serial}/{object_key}")
            continue

        # Get existing state
        server_obj = state_service.get_object(serial, object_key)
        existing_value = server_obj.value if server_obj else {}
        merged_value = {**existing_value, **value}

        # Store weave_device_id if provided
        if weave_device_id:
            merged_value["weave_device_id"] = weave_device_id

        # Preserve fan timer for device objects
        object_type, _ = parse_object_key(object_key)
        if object_type == "device":
            device_object_changed = True
            merged_value = preserve_fan_timer_state(existing_value, merged_value, serial)

        # Check if values changed
        values_changed = not server_obj or not _values_equal(server_obj.value, merged_value)
        new_revision = (
            ((server_obj.object_revision if server_obj else 0) + 1)
            if values_changed
            else (server_obj.object_revision if server_obj else 0)
        )
        new_timestamp = int(time.time() * 1000)

        # Save update
        new_obj = DeviceObject(
            serial=serial,
            object_key=object_key,
            object_revision=new_revision,
            object_timestamp=new_timestamp,
            value=merged_value,
            updated_at=datetime.utcnow(),
        )
        await state_service.upsert_object(new_obj)

        # Build response
        response_obj: dict[str, Any] = {
            "object_revision": new_obj.object_revision,
            "object_timestamp": new_obj.object_timestamp,
            "object_key": new_obj.object_key,
        }
        if values_changed:
            response_obj["value"] = new_obj.value

        response_objects.append(response_obj)

    # Sync user state if device object changed
    if device_object_changed:
        device_owner = await state_service.storage.get_device_owner(serial)
        if device_owner:
            await state_service.storage.update_user_away_status(device_owner.user_id)
            await state_service.storage.sync_user_weather_from_device(device_owner.user_id)

    # Notify subscribers
    if response_objects:
        notified = await subscription_manager.notify_all_chunked(serial, response_objects)
        logger.debug(
            f"PUT: Notified {notified} subscriber(s) for {serial}, "
            f"{len(response_objects)} object(s) updated"
        )

    return web.json_response({"objects": response_objects})


def _values_equal(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    """Check if two value dictionaries are equal."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return a == b


def create_transport_routes(
    app: web.Application,
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
    device_availability: DeviceAvailability,
) -> None:
    """Register transport routes."""
    app["state_service"] = state_service
    app["subscription_manager"] = subscription_manager
    app["device_availability"] = device_availability

    # Device object listing - specific route first
    app.router.add_get("/nest/transport/device/{serial}", handle_transport_get)

    # Long-poll subscription - both direct and versioned paths
    app.router.add_post("/nest/transport", handle_transport_subscribe)
    app.router.add_post("/nest/transport/{version}/subscribe", handle_transport_subscribe)

    # State updates - both direct and versioned paths
    app.router.add_post("/nest/transport/put", handle_transport_put)
    app.router.add_post("/nest/transport/{version}/put", handle_transport_put)

    # Legacy czfe paths - catch all for GET requests to transport
    app.router.add_get("/nest/transport/{path:.*}", handle_transport_get)
