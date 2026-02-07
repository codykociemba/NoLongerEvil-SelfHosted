"""Nest transport endpoint - device state management and subscriptions.

Subscribe Flow (Chunked Mode - Per Protocol Spec):
1. Receive POST /subscribe with client's bucket revisions
2. Compare revisions against server state
3. Send HTTP headers immediately with Transfer-Encoding: chunked
4. Device receives headers → becomes eligible to sleep immediately
5. Server either:
   - Sends JSON body immediately if updates available
   - Holds connection open indefinitely, waiting for server-side data to push
6. When server has data to push: send body chunk, device wakes instantly
7. Device processes data, resubscribes

Key timing (configurable):
- suspend_time_max: Device's sleep timer (e.g., 600s). Device wakes and resubscribes
  even if no data was pushed. This is the FALLBACK mechanism.
- connection_hold_timeout: Server holds connection suspend_time_max + 60s buffer.
  Server should NEVER close before device's wake timer fires.

IMPORTANT: Tickles (empty responses) are NOT used for normal operation.
Tickles are for administrative use only (server shutdown, load balancer migration).
For normal operation, server holds connection until data is available to push.

Protocol compliance notes:
- Uses Transfer-Encoding: chunked
- Supports both request formats: objects array and named bucket fields
- Implements X-nl-defer-device-window for batching rapid dial changes
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


# Known bucket types from Nest protocol
KNOWN_BUCKET_TYPES = {
    "device", "shared", "structure", "schedule", "custom_schedule", "user",
    "topaz", "demand_response", "demand_response_event", "where", "kryptonite",
    "diagnostics", "device_alert_dialog", "servicegroup", "link", "message",
    "tuneups", "utility", "diamond_sensor_config", "diamond_sensor_event",
    "rate_plan", "tou", "demand_charge", "demand_charge_event", "hvac_partner",
    "rcs_settings", "cloud_algo",
}


def parse_subscribe_body(body: dict[str, Any]) -> tuple[str, bool, list[dict[str, Any]]]:
    """Parse subscribe request body supporting both formats.

    Format 1 (named bucket fields):
    {
        "chunked": true,
        "session": "session_id",
        "device": {"object_key": "device.SERIAL", "object_revision": 123, ...},
        "shared": {"object_key": "shared.SERIAL", ...}
    }

    Format 2 (objects array - alternate):
    {
        "chunked": true,
        "session": "session_id",
        "objects": [{"object_key": "device.SERIAL", ...}, ...]
    }

    Returns:
        Tuple of (session, chunked, objects_list)
    """
    session = body.get("session", "")
    chunked = body.get("chunked", False)

    # Check for objects array first
    if "objects" in body and isinstance(body["objects"], list):
        return session, chunked, body["objects"]

    # Parse named bucket fields
    objects: list[dict[str, Any]] = []
    for key, value in body.items():
        if key in KNOWN_BUCKET_TYPES and isinstance(value, dict):
            # This is a bucket field
            if "object_key" in value:
                objects.append(value)
            else:
                # Bucket field without object_key - skip or log
                logger.debug(f"Bucket field '{key}' missing object_key, skipping")

    return session, chunked, objects


def parse_put_body(body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Parse PUT request body supporting both formats.

    Format 1 (objects array):
    {"session": "...", "objects": [{"object_key": "...", "value": {...}}]}

    Format 2 (bucket-keyed - per spec):
    {"session": "...", "shared.SERIAL": {"object_key": "...", "target_temperature": 21.5}}

    In bucket-keyed format, data fields are inline with metadata (object_key,
    base_object_revision, if_object_revision). We extract inline fields into
    a value dict.

    Returns:
        Tuple of (session, objects_list)
    """
    session = body.get("session", "")

    # Check for objects array first
    if "objects" in body and isinstance(body["objects"], list):
        return session, body["objects"]

    # Parse bucket-keyed format
    objects: list[dict[str, Any]] = []
    metadata_fields = {"object_key", "base_object_revision", "if_object_revision"}

    for key, value in body.items():
        if key == "session":
            continue
        # Keys like "shared.SERIAL" or "device.SERIAL"
        if isinstance(value, dict) and "object_key" in value:
            # Extract inline fields into value dict (excluding metadata)
            inline_value = {k: v for k, v in value.items() if k not in metadata_fields}
            objects.append({
                "object_key": value["object_key"],
                "base_object_revision": value.get("base_object_revision"),
                "if_object_revision": value.get("if_object_revision"),
                "value": inline_value if inline_value else None,
            })

    return session, objects


def format_object_for_response(obj: DeviceObject, include_value: bool = True) -> dict[str, Any]:
    """Format a device object for JSON response.

    IMPORTANT: Field order matters! object_revision and object_timestamp MUST
    appear before object_key in the JSON, or the device may not apply them correctly.
    """
    result: dict[str, Any] = {
        "object_revision": obj.object_revision,
        "object_timestamp": obj.object_timestamp,
        "object_key": obj.object_key,
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


def _make_response_headers(include_disable_defer: bool = False) -> dict[str, str]:
    """Create standard response headers for Nest protocol.

    Args:
        include_disable_defer: If True, include X-nl-disable-defer-window header.
                               Use when pushing temperature/mode changes to get
                               immediate device confirmation.
    """
    headers = {
        "X-nl-service-timestamp": str(int(time.time() * 1000)),
        "X-nl-suspend-time-max": str(settings.suspend_time_max),
        "X-nl-defer-device-window": str(settings.defer_device_window),
    }

    if include_disable_defer:
        headers["X-nl-disable-defer-window"] = str(settings.disable_defer_window)

    return headers


def _contains_temperature_fields(objects: list[DeviceObject]) -> bool:
    """Check if any objects contain temperature-related fields.

    Used to determine if X-nl-disable-defer-window should be sent,
    which triggers immediate device confirmation instead of waiting
    for the defer_device_window delay.
    """
    temp_fields = {
        "target_temperature",
        "target_temperature_high",
        "target_temperature_low",
        "target_temperature_type",
        "hvac_mode",
    }
    for obj in objects:
        if obj.value and any(field in obj.value for field in temp_fields):
            return True
    return False


async def handle_transport_subscribe(request: web.Request) -> web.StreamResponse:
    """Handle POST /nest/transport - subscribe to device updates.

    This is the main Nest protocol endpoint. It handles:
    1. Device sending state updates (when value provided with rev/ts = 0)
    2. Device subscribing to updates (long-poll with chunked response)
    3. Server responding with outdated objects if device is behind

    Chunked Response Flow:
    1. Send headers with Transfer-Encoding: chunked immediately
    2. Device can sleep after receiving headers
    3. Server holds connection, waiting for data or timeout
    4. Send JSON body when data available or on tickle timeout
    """
    serial = extract_serial_from_request(request)
    if not serial:
        return web.json_response({"error": "Device serial required"}, status=400)

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    # Parse body supporting both formats (named fields or objects array)
    session, chunked, objects = parse_subscribe_body(body)
    if not session:
        session = f"session_{serial}_{int(time.time() * 1000)}"
    weave_device_id = extract_weave_device_id(request)

    # Log device-reported wake duration (X-nl-longest-wake is device-to-server only)
    longest_wake = request.headers.get("X-nl-longest-wake")
    if longest_wake:
        logger.debug(f"Device {serial} reported longest-wake: {longest_wake}s")

    logger.debug(
        f"Subscribe from {serial}: chunked={chunked}, {len(objects)} objects, session={session}, "
        f"suspend_max={settings.suspend_time_max}s, connection_hold={settings.connection_hold_timeout}s"
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
    # Per rev/ts spec: timestamp is sole authority
    outdated_objects: list[DeviceObject] = []
    objects_to_merge: list[tuple[dict[str, Any], DeviceObject]] = []

    for i, client_obj in enumerate(processed_client_objects):
        response_obj = response_objects[i]
        client_timestamp = client_obj.get("object_timestamp", 0)
        object_key = client_obj.get("object_key", "")

        # Skip user.* objects - device doesn't accept them and loops forever
        if object_key.startswith("user."):
            continue

        # Use timestamp-only comparison (no revision tiebreaker)
        server_newer = _is_server_newer(
            response_obj.object_timestamp,
            client_timestamp,
        )

        if server_newer:
            # Server has newer data - send our data to device
            outdated_objects.append(response_obj)
        elif client_timestamp > response_obj.object_timestamp:
            # Client has newer data (timestamp only, no revision tiebreaker)
            # Equal timestamps = already synced, no merge needed
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

    # =========================================================================
    # Response handling - chunked vs non-chunked mode
    # =========================================================================
    # Chunked vs non-chunked:
    # - Chunked: Send headers immediately, device can sleep, then send body
    # - Non-chunked: Must send body within 7 seconds or device times out
    # =========================================================================

    if not chunked:
        # Non-chunked mode - respond immediately (7s timeout on device side)
        if outdated_objects:
            formatted_objs = [format_object_for_response(obj) for obj in outdated_objects]
            return web.json_response(
                {"objects": formatted_objs},
                headers=_make_response_headers(),
            )
        return web.json_response(
            {"objects": []},
            headers=_make_response_headers(),
        )

    # =========================================================================
    # Chunked mode
    # =========================================================================
    # 1. Send headers with Transfer-Encoding: chunked immediately
    # 2. Device receives headers → becomes eligible to sleep
    # 3. If updates available: send body immediately
    # 4. If no updates: hold connection, wait for data or timeout
    # 5. Send body (data or empty tickle), close connection
    # =========================================================================

    # Determine if we should disable defer window (pushing temp changes)
    # Must check BEFORE response.prepare() since headers are sent there
    include_disable_defer = bool(outdated_objects) and _contains_temperature_fields(
        outdated_objects
    )

    # Create chunked streaming response
    response_headers = {
        "Content-Type": "application/json",
        "Transfer-Encoding": "chunked",
        "X-nl-service-timestamp": str(int(time.time() * 1000)),
        "X-nl-suspend-time-max": str(settings.suspend_time_max),
        "X-nl-defer-device-window": str(settings.defer_device_window),
    }
    if include_disable_defer:
        response_headers["X-nl-disable-defer-window"] = str(settings.disable_defer_window)

    response = web.StreamResponse(status=200, headers=response_headers)

    # Send headers immediately - device can now sleep
    await response.prepare(request)
    logger.debug(
        f"Sent chunked headers to {serial}, device can now sleep "
        f"(disable_defer={include_disable_defer})"
    )

    # If we have outdated objects, send them immediately
    if outdated_objects:
        formatted_objs = [format_object_for_response(obj) for obj in outdated_objects]
        logger.debug(
            f"Sending {len(outdated_objects)} outdated object(s) immediately for {serial}"
        )
        body_data = json.dumps({"objects": formatted_objs}).encode("utf-8")
        await response.write(body_data)
        await response.write_eof()
        return response

    # No immediate updates - hold connection and wait for server-push
    subscription = await subscription_manager.add_long_poll_subscription(serial, session)

    if subscription is None:
        # Too many subscriptions - send empty response and close
        logger.warning(f"Too many subscriptions for {serial}")
        await response.write(json.dumps({"objects": []}).encode("utf-8"))
        await response.write_eof()
        return response

    logger.debug(
        f"Holding chunked connection for {serial} (subscription={subscription.id}, session={session}), "
        f"server hold timeout at {settings.connection_hold_timeout:.0f}s (device wakes at {settings.suspend_time_max}s)"
    )

    # Direct queue access - no lookup needed
    notify_queue = subscription.notify_queue
    data_sent = False

    try:
        # Wait for data - hold connection until data arrives or device disconnects
        # Let device wake timer fire naturally
        # DO NOT send tickle/empty response - that's for administrative use only
        try:
            # Use connection_hold_timeout which is > suspend_time_max
            # This ensures we never close before device's wake timer fires
            changed_objects = await asyncio.wait_for(
                notify_queue.get(),
                timeout=settings.connection_hold_timeout,
            )
            # Real data arrived - send it to wake the device
            logger.debug(f"Subscription {subscription.id}: pushing {len(changed_objects)} objects to wake device")
            body_bytes = json.dumps({"objects": changed_objects}).encode("utf-8")
            await response.write(body_bytes)
            data_sent = True
            logger.info(
                f"Subscription {subscription.id}: write completed, sent {len(body_bytes)} bytes to {serial}"
            )

        except asyncio.TimeoutError:
            # Server-side timeout expired AFTER device should have already woken up
            # The device's suspend_time_max timer fires, device wakes, sends new subscribe
            # If we get here, the device must have disconnected without us noticing
            # This is expected behavior - just close the connection quietly
            # DO NOT send tickle - tickles force immediate reconnect
            logger.debug(
                f"Subscription {subscription.id}: server hold timeout at {settings.connection_hold_timeout:.0f}s - "
                f"device should have already resubscribed (suspend_time_max={settings.suspend_time_max}s)"
            )

    except (asyncio.CancelledError, ConnectionResetError, ConnectionError) as e:
        # Connection closed by device (it went to sleep) - this is normal
        logger.info(f"Subscription {subscription.id}: connection closed ({type(e).__name__}): {e}")

    finally:
        logger.debug(f"Removing subscription {subscription.id} for {serial}")
        await subscription_manager.remove_long_poll_subscription(subscription)

    # Only terminate chunked response if we actually sent data
    # Empty body (0\r\n\r\n) is a "tickle" that forces reconnect
    # On timeout, device has already resubscribed, so don't send tickle
    if data_sent:
        try:
            await response.write_eof()
            logger.debug(f"Subscription {subscription.id}: write_eof completed for {serial}")
        except (ConnectionResetError, ConnectionError) as e:
            logger.info(f"Subscription {subscription.id}: write_eof failed ({type(e).__name__}): {e}")

    return response


async def handle_transport_put(request: web.Request) -> web.Response:
    """Handle POST /nest/transport/put - device state updates."""
    serial = extract_serial_from_request(request)
    if not serial:
        return web.json_response({"error": "Device serial required"}, status=400)

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    # Parse body supporting both formats (objects array or bucket-keyed)
    _session, objects = parse_put_body(body)
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

        # Check conditional write (if_object_revision must match server's revision)
        if_rev = client_obj.get("if_object_revision")
        if if_rev is not None:
            server_rev = server_obj.object_revision if server_obj else 0
            if if_rev != server_rev:
                # Per spec: return 200 OK with server state for device reconciliation
                # Device compares timestamps and decides which version wins
                logger.debug(
                    f"PUT: Conditional write conflict for {object_key}: "
                    f"if_object_revision={if_rev} != server_revision={server_rev}, "
                    f"returning server state for reconciliation"
                )
                conflict_response = {
                    "object_revision": server_obj.object_revision if server_obj else 0,
                    "object_timestamp": server_obj.object_timestamp if server_obj else 0,
                    "object_key": object_key,
                    "value": server_obj.value if server_obj else {},
                }
                return web.json_response(
                    {"objects": [conflict_response]},
                    status=200,  # NOT 409 - device reconciles via timestamp comparison
                    headers=_make_response_headers(),
                )

        # Log base_object_revision (informational only, no rejection)
        base_rev = client_obj.get("base_object_revision")
        if base_rev is not None:
            logger.debug(f"PUT: {object_key} base_object_revision={base_rev}")

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

    # Notify subscribers (pushes to long-poll held connections)
    if response_objects:
        notified = await subscription_manager.notify_subscribers_with_dicts(
            serial, response_objects
        )
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


def _is_server_newer(server_ts: int, client_ts: int) -> bool:
    """Determine if server data is newer than client data.

    Per the rev/ts protocol spec:
    https://github.com/cjserio/nest-thermostat-protocol-docs/blob/main/server_rev_ts_guide.md

    1. Compare timestamps only - larger timestamp wins
    2. Zero timestamp means "no data" - always yields to non-zero
    3. Equal timestamps means already synced - no action needed
    """
    # Special case: client ts=0 means "no data", server should send its data
    if client_ts == 0:
        return True

    # Special case: server ts=0 means "no data", server has nothing to send
    if server_ts == 0:
        return False

    # Timestamp comparison only - no revision tiebreaker
    # Equal timestamps = already synced
    return server_ts > client_ts


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
