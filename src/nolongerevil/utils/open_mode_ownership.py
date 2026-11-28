"""Automatic device ownership in open mode (require_device_pairing = false).

In open mode a thermostat is treated as paired without an owner record, so the
user-scoped buckets (user.<id>, structure.<id>) never get set up. A real
thermostat that talks to the transport or upload endpoints is assigned to the
built-in homeassistant user instead. The record is marked as an open-mode
owner, so it is removed again at startup when pairing is required.

Before the first assignment, the device's structure.default bucket (which
holds its away/eco state in open mode) is copied to structure.homeassistant so
the device keeps its current state.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from aiohttp import web

from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.serial_parser import (
    extract_serial_from_basic_auth,
    extract_serial_from_client_id,
)
from nolongerevil.lib.types import DeviceObject, DeviceOwner
from nolongerevil.models.base import now_ms
from nolongerevil.utils.structure_assignment import derive_structure_id

if TYPE_CHECKING:
    from nolongerevil.services.abstract_device_state_manager import AbstractDeviceStateManager
    from nolongerevil.services.device_state_service import DeviceStateService

logger = get_logger(__name__)

OPEN_MODE_USER_ID = "homeassistant"
DEFAULT_STRUCTURE_KEY = "structure.default"


def is_device_transport_request(request: web.Request) -> bool:
    """Return True for transport POSTs (subscribe, PUT) and uploads."""
    path = request.path.lower()
    return ("/nest/transport" in path and request.method == "POST") or "/nest/upload" in path


def thermostat_serial(request: web.Request) -> str | None:
    """Return the serial a thermostat identifies itself with on transport.

    Only the device's own credentials count (Basic Auth username, or the
    X-nl-client-id header it sends with default credentials). Serials from a
    query string, URL path or custom header are ignored.
    """
    serial = extract_serial_from_basic_auth(request.headers.get("Authorization"))
    if serial:
        return serial
    return extract_serial_from_client_id(request.headers.get("X-nl-client-id"))


async def _move_default_structure(
    state_service: "DeviceStateService", serial: str, user_id: str
) -> None:
    """Copy structure.default to the owner's structure bucket, once."""
    default_obj = state_service.get_object(serial, DEFAULT_STRUCTURE_KEY)
    if not default_obj or not default_obj.value:
        return

    structure_key = f"structure.{derive_structure_id(user_id)}"
    if state_service.get_object(serial, structure_key):
        return

    value = dict(default_obj.value)
    value.setdefault("name", "Home")
    devices = list(value.get("devices") or [])
    if serial not in devices:
        devices.append(serial)
    value["devices"] = devices

    await state_service.upsert_object(
        DeviceObject(
            serial=serial,
            object_key=structure_key,
            object_revision=1,
            object_timestamp=now_ms(),
            value=value,
            updated_at=datetime.now(),
        )
    )
    logger.info(f"Moved {DEFAULT_STRUCTURE_KEY} to {structure_key} for open-mode device {serial}")


async def assign_open_mode_owner(
    request: web.Request,
    storage: "AbstractDeviceStateManager",
    state_service: "DeviceStateService | None",
) -> bool:
    """Assign a real, unowned thermostat to the homeassistant user.

    Returns:
        True if an owner record was created
    """
    if not is_device_transport_request(request) or state_service is None:
        return False

    serial = thermostat_serial(request)
    if not serial:
        return False

    # Only a device the server has already heard from (it has reported its
    # own device bucket) is assigned.
    if not state_service.get_object(serial, f"device.{serial}"):
        return False

    if await storage.get_device_owner(serial):
        return False

    await _move_default_structure(state_service, serial, OPEN_MODE_USER_ID)
    await storage.set_device_owner(
        DeviceOwner(
            serial=serial,
            user_id=OPEN_MODE_USER_ID,
            created_at=datetime.now(),
            open_mode=True,
        )
    )
    logger.info(f"Open-mode device {serial} automatically assigned to user {OPEN_MODE_USER_ID}")
    return True
