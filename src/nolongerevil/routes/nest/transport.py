"""Nest transport endpoint - device state management and subscriptions.

Subscribe Flow:
1. Receive POST /subscribe with client's bucket revisions
2. Compare revisions against server state
3. IF newer data exists: Send response immediately (headers + JSON + close)
4. IF NO newer data: Hold connection silently (no headers, no body)
5. While holding, wait for:
   - New data arrives → send response, close
   - Timeout approaching (80% of SUSPEND_TIME_MAX) → send tickle, close
   - Connection drops → clean up

The device enters sleep mode when no response is being received.
X-nl-suspend-time-max tells device how long to sleep after disconnect.
Worst-case push latency = single hold cycle (~48s with 60s suspend max).
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Any

from aiohttp import web

from nolongerevil.config.environment import settings
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
    """Format a device object for JSON response.

    IMPORTANT: Field order matters! The device parser triggers timestamp/revision
    application when it encounters "object_key". If object_key comes before
    object_revision/object_timestamp, the device applies 0s (defaults) instead
    of the actual values.
    """
    result: dict[str, Any] = {
        # object_revision and object_timestamp MUST come before object_key
        # (device parses fields in order and applies ts/rev when it sees object_key)
        "object_revision": obj.object_revision,
        "object_timestamp": obj.object_timestamp,
        "object_key": obj.object_key,
        "serial": obj.serial,
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

    return web.json_response(
        {"objects": response_objects},
        headers=_make_response_headers(),
    )


def _make_response_headers() -> dict[str, str]:
    """Create standard response headers for Nest protocol."""
    return {
        "X-nl-service-timestamp": str(int(time.time() * 1000)),
        "X-nl-suspend-time-max": str(settings.suspend_time_max),
    }


async def handle_transport_subscribe(request: web.Request) -> web.Response:
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

    # Log device-reported wake duration (X-nl-longest-wake is device-to-server only)
    longest_wake = request.headers.get("X-nl-longest-wake")
    if longest_wake:
        logger.debug(f"Device {serial} reported longest-wake: {longest_wake}s")

    logger.debug(
        f"Subscribe from {serial}: chunked={chunked}, {len(objects)} objects, session={session}, "
        f"suspend_max={settings.suspend_time_max}s, tickle_at={settings.tickle_timeout:.0f}s"
    )
    for obj in objects:  # Log all objects
        logger.debug(
            f"  Object: key={obj.get('object_key')} rev={obj.get('object_revision')} ts={obj.get('object_timestamp')}"
        )

    if not isinstance(objects, list):
        return web.Response(text="Invalid request: objects array required", status=400)

    state_service: DeviceStateService = request.app["state_service"]
    subscription_manager: SubscriptionManager = request.app["subscription_manager"]

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

        # Special case: target_change_pending is transient - device clears it after acknowledging
        # Always accept target_change_pending:false from device to avoid update loops
        if (
            value is not None
            and object_key.startswith("shared.")
            and value.get("target_change_pending") is False
            and server_obj
            and server_obj.value.get("target_change_pending") is True
        ):
            logger.debug(f"Device cleared target_change_pending for {object_key}")
            updated_value = {**server_obj.value, "target_change_pending": False}
            await state_service.upsert_object(
                DeviceObject(
                    serial=serial,
                    object_key=object_key,
                    object_revision=server_obj.object_revision,
                    object_timestamp=server_obj.object_timestamp,
                    value=updated_value,
                    updated_at=datetime.now(),
                )
            )
            # Update server_obj reference for later use
            server_obj = state_service.get_object(serial, object_key)

        if is_update:
            # Device is sending us an update
            existing_value = server_obj.value if server_obj else {}
            merged_value = {**existing_value, **value}

            # Store weave_device_id if provided
            if weave_device_id:
                merged_value["weave_device_id"] = weave_device_id

            # Apply object-type specific logic
            if object_key == f"device.{serial}":
                # Preserve fan timer state for device objects
                merged_value = preserve_fan_timer_state(existing_value, merged_value, serial)

                # Auto-assign structure_id based on device owner if needed
                from nolongerevil.utils.structure_assignment import needs_structure_id

                if needs_structure_id(merged_value):
                    device_owner = await state_service.storage.get_device_owner(serial)
                    if device_owner:
                        result = assign_structure_id(merged_value, device_owner.user_id, serial)
                        if result.get("assigned"):
                            await state_service.storage.update_user_away_status(
                                device_owner.user_id
                            )
                            await state_service.storage.sync_user_weather_from_device(
                                device_owner.user_id
                            )

                # Sync user state when away or postal_code changes
                if "away" in value or "postal_code" in value:
                    device_owner = await state_service.storage.get_device_owner(serial)
                    if device_owner:
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
                    updated_at=datetime.now(),
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
                    updated_at=datetime.now(),
                )
            )

    # Find outdated objects (server has newer data than client)
    outdated_objects: list[DeviceObject] = []
    objects_to_merge: list[tuple[dict[str, Any], DeviceObject]] = []

    for i, client_obj in enumerate(processed_client_objects):
        response_obj = response_objects[i]
        client_revision = client_obj.get("object_revision", 0)
        client_timestamp = client_obj.get("object_timestamp", 0)

        # If client sent rev=0 and ts=0, they want our full state (resync request)
        if client_revision == 0 and client_timestamp == 0:
            object_key = client_obj.get("object_key", "")
            # Skip user.* objects - device doesn't accept them and loops forever
            if object_key.startswith("user."):
                continue
            # Send our stored state - no need to refresh timestamp since the real
            # issue was JSON field order (object_key must come after ts/rev)
            outdated_objects.append(response_obj)
            continue

        # Only consider server data "newer" if revision is higher
        # Timestamp alone shouldn't trigger updates - matching revisions means matching data
        server_revision_higher = response_obj.object_revision > client_revision

        if server_revision_higher:
            # Server has newer data - send our data to device
            object_key = client_obj.get("object_key", "")
            # Skip user.* objects - device doesn't accept them and loops forever
            if object_key.startswith("user."):
                continue
            # Send our stored state with its existing timestamp
            outdated_objects.append(response_obj)
        elif client_revision > response_obj.object_revision:
            # Client has newer data - merge their data
            objects_to_merge.append((client_obj, response_obj))

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
                    updated_at=datetime.now(),
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
                f"  Response: key={obj.get('object_key')} rev={obj.get('object_revision')} ts={obj.get('object_timestamp')} value={obj.get('value')}"
            )
        response_data = {"objects": formatted_objs}
        return web.json_response(
            response_data,
            headers={
                "X-nl-service-timestamp": str(int(time.time() * 1000)),
                "X-nl-suspend-time-max": str(settings.suspend_time_max),
            },
        )

    # =========================================================================
    # Hold connection silently until data or timeout
    # =========================================================================
    # No immediate response. We hold the TCP connection open without sending
    # any HTTP response. The device can sleep during this time.
    #
    # We respond when:
    # 1. New data arrives (via subscription notification)
    # 2. Timeout approaching (settings.tickle_timeout seconds)
    # 3. Connection drops (cleanup)
    # =========================================================================

    if not chunked:
        # Non-chunked mode - just respond with empty objects
        return web.json_response(
            {"objects": []},
            headers=_make_response_headers(),
        )

    # Build subscribed keys map for filtering notifications
    subscribed_keys = {
        obj.get("object_key"): obj.get("object_revision", 0)
        for obj in objects
        if obj.get("object_key")
    }

    # Add to subscription manager (creates the notification queue)
    added = await subscription_manager.add_silent_subscription(
        serial, session, subscribed_keys
    )

    if not added:
        return web.json_response(
            {"error": "Too many subscriptions"},
            status=429,
            headers=_make_response_headers(),
        )

    logger.debug(f"Holding connection silently for {serial} (session: {session}), timeout at {settings.tickle_timeout:.0f}s")

    notify_queue = subscription_manager.get_subscription_queue(serial, session)

    if notify_queue is None:
        logger.error(f"Subscription {session}: queue not found after adding")
        return web.json_response(
            {"objects": []},
            headers=_make_response_headers(),
        )

    try:
        # Wait for data or timeout
        try:
            changed_objects = await asyncio.wait_for(
                notify_queue.get(), timeout=settings.tickle_timeout
            )
            # Real data arrived - send it
            logger.debug(f"Subscription {session}: sending {len(changed_objects)} pushed objects")
            return web.json_response(
                {"objects": changed_objects},
                headers=_make_response_headers(),
            )

        except asyncio.TimeoutError:
            # Timeout - send tickle response (empty objects)
            logger.debug(f"Subscription {session}: tickle timeout at {settings.tickle_timeout:.0f}s")
            return web.json_response(
                {"objects": []},
                headers=_make_response_headers(),
            )

    except (asyncio.CancelledError, ConnectionResetError, ConnectionError) as e:
        logger.debug(f"Subscription {session}: connection error: {e}")
        return web.json_response(
            {"objects": []},
            headers=_make_response_headers(),
        )
    finally:
        logger.debug(f"Removing subscription {session} for {serial}")
        await subscription_manager.remove_silent_subscription(serial, session)


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
        if object_key == f"device.{serial}":
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
            updated_at=datetime.now(),
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

    # Notify subscribers (pushes to silently held connections)
    if response_objects:
        notified = await subscription_manager.notify_all_subscribers(serial, response_objects)
        logger.debug(
            f"PUT: Notified {notified} subscriber(s) for {serial}, "
            f"{len(response_objects)} object(s) updated"
        )

    # Include shared.{serial} in PUT response to ensure device gets temperature updates
    # This provides a reliable sync point since PUT always gets a response
    shared_key = f"shared.{serial}"
    shared_obj = state_service.get_object(serial, shared_key)
    if shared_obj:
        # Check if shared wasn't already in response
        shared_keys_in_response = [obj.get("object_key") for obj in response_objects]
        if shared_key not in shared_keys_in_response:
            response_objects.append({
                "object_key": shared_obj.object_key,
                "object_revision": shared_obj.object_revision,
                "object_timestamp": shared_obj.object_timestamp,
                "value": shared_obj.value,
            })
            logger.debug(f"PUT: Added shared.{serial} to response (rev={shared_obj.object_revision})")

    return web.json_response(
        {"objects": response_objects},
        headers=_make_response_headers(),
    )


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

    # Long-poll subscription - both direct and versioned paths (e.g., /nest/transport, /nest/transport/v7/subscribe)
    app.router.add_post("/nest/transport", handle_transport_subscribe)
    app.router.add_post("/nest/transport/{version}/subscribe", handle_transport_subscribe)

    # State updates - both direct and versioned paths (e.g., /nest/transport/put, /nest/transport/v7/put)
    app.router.add_post("/nest/transport/put", handle_transport_put)
    app.router.add_post("/nest/transport/{version}/put", handle_transport_put)

    # Legacy czfe paths - catch all for GET requests to transport
    app.router.add_get("/nest/transport/{path:.*}", handle_transport_get)
