"""Tests for MAC-to-serial alias resolution.

Some devices (e.g. Display-2.12) only identify themselves by MAC address until
their first /subscribe, where the session ID is formatted as <mac><serial>.
The server extracts the real serial from that session ID, remembers the
mapping (in-memory + persisted), migrates any objects already stored under the
MAC, and rewrites MAC-keyed object_keys to the real serial going forward.
"""

import json
import time
from base64 import b64encode
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from aiohttp import web

from nolongerevil.config.environment import settings
from nolongerevil.lib.mac_alias import objects_to_device_keys, to_device_object_key
from nolongerevil.lib.types import DeviceObject, DeviceOwner
from nolongerevil.middleware.device_auth import (
    TIER_PAIRED,
    TIER_PENDING,
    TIER_UNKNOWN,
    create_device_auth_middleware,
)
from nolongerevil.middleware.device_heartbeat import create_device_heartbeat_middleware
from nolongerevil.routes.nest.transport import handle_transport_put, handle_transport_subscribe
from nolongerevil.services.device_availability import DeviceAvailability
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.subscription_manager import SubscriptionManager

MAC = "11B2334455D6"
MAC_LOWER = MAC.lower()
REAL_SERIAL = "02AA01AB501203EQ"
REAL_SERIAL_LOWER = REAL_SERIAL.lower()


def _auth_header(username: str) -> str:
    return "Basic " + b64encode(f"{username}:password".encode()).decode()


def _make_subscribe_request(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
    mac_to_serial: dict[str, str],
    body: dict,
    auth_serial: str = MAC,
) -> Mock:
    """Build a mock aiohttp request for handle_transport_subscribe (non-chunked)."""
    req = Mock(spec=web.Request)
    req.headers = {"Authorization": _auth_header(auth_serial)}
    req.json = AsyncMock(return_value=body)
    req.app = {
        "state_service": state_service,
        "subscription_manager": subscription_manager,
        "mac_to_serial": mac_to_serial,
    }
    return req


async def _subscribe(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
    mac_to_serial: dict[str, str],
    body: dict,
    auth_serial: str = MAC,
) -> dict:
    """Call handle_transport_subscribe (non-chunked) and return the parsed body."""
    req = _make_subscribe_request(state_service, subscription_manager, mac_to_serial, body, auth_serial)
    resp = await handle_transport_subscribe(req)
    assert resp.status == 200
    return json.loads(resp.body)


# ---------------------------------------------------------------------------
# Subscribe: first contact establishes the MAC -> serial mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_resolves_mac_via_session_id(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> None:
    """A subscribe whose session ID is <mac><serial> records the mapping."""
    mac_to_serial: dict[str, str] = {}
    session_id = MAC_LOWER + REAL_SERIAL

    await _subscribe(
        state_service,
        subscription_manager,
        mac_to_serial,
        {"chunked": False, "session": session_id, "objects": []},
    )

    assert mac_to_serial[MAC_LOWER] == REAL_SERIAL

    # Mapping is persisted so the PUT handler can resolve it after a restart
    mapping_obj = state_service.get_object(f"mac_alias.{MAC_LOWER}", "mac_alias")
    assert mapping_obj is not None
    assert mapping_obj.value["serial"] == REAL_SERIAL


@pytest.mark.asyncio
async def test_subscribe_migrates_objects_from_mac_to_real_serial(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> None:
    """Pre-existing MAC-keyed objects are moved to the resolved serial."""
    await state_service.upsert_object(
        DeviceObject(
            serial=MAC,
            object_key=f"device.{MAC_LOWER}",
            object_revision=2,
            object_timestamp=1000,
            value={"current_humidity": 50},
            updated_at=datetime.now(),
        )
    )

    mac_to_serial: dict[str, str] = {}
    session_id = MAC_LOWER + REAL_SERIAL

    await _subscribe(
        state_service,
        subscription_manager,
        mac_to_serial,
        {"chunked": False, "session": session_id, "objects": []},
    )

    # Old MAC device is gone
    assert state_service.get_objects_by_serial(MAC) == []

    # Object now lives under the real serial with object_key rewritten
    migrated = state_service.get_object(REAL_SERIAL, f"device.{REAL_SERIAL_LOWER}")
    assert migrated is not None
    assert migrated.value == {"current_humidity": 50}
    assert migrated.object_revision == 2
    assert migrated.object_timestamp == 1000


@pytest.mark.asyncio
async def test_subscribe_rewrites_mac_object_keys_in_same_request(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> None:
    """An object in the same subscribe request keyed by MAC is stored under
    the resolved real serial."""
    mac_to_serial: dict[str, str] = {}
    session_id = MAC_LOWER + REAL_SERIAL

    body = await _subscribe(
        state_service,
        subscription_manager,
        mac_to_serial,
        {
            "chunked": False,
            "session": session_id,
            "objects": [
                {
                    "object_key": f"device.{MAC_LOWER}",
                    "object_revision": 0,
                    "object_timestamp": 0,
                    "value": {"current_humidity": 55},
                }
            ],
        },
    )

    # Response echoes back the rewritten key, not the MAC-based one
    assert body["objects"]
    assert body["objects"][0]["object_key"] == f"device.{REAL_SERIAL_LOWER}"

    stored = state_service.get_object(REAL_SERIAL, f"device.{REAL_SERIAL_LOWER}")
    assert stored is not None
    assert stored.value == {"current_humidity": 55}


@pytest.mark.asyncio
async def test_subscribe_without_mac_session_does_not_alias(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> None:
    """A normal device (serial auth, non-MAC session) is left untouched."""
    mac_to_serial: dict[str, str] = {}

    await _subscribe(
        state_service,
        subscription_manager,
        mac_to_serial,
        {"chunked": False, "session": "regular-session-id", "objects": []},
        auth_serial=REAL_SERIAL,
    )

    assert mac_to_serial == {}
    assert state_service.get_object(f"mac_alias.{MAC_LOWER}", "mac_alias") is None


@pytest.mark.asyncio
async def test_subscribe_session_equal_to_mac_does_not_alias(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> None:
    """If the session ID is just the MAC (no trailing serial), no alias is created."""
    mac_to_serial: dict[str, str] = {}

    await _subscribe(
        state_service,
        subscription_manager,
        mac_to_serial,
        {"chunked": False, "session": MAC_LOWER, "objects": []},
    )

    assert mac_to_serial == {}


# ---------------------------------------------------------------------------
# PUT: resolves MAC-only requests via in-memory cache or persisted mapping
# ---------------------------------------------------------------------------


def _make_put_request(
    state_service: DeviceStateService,
    mac_to_serial: dict[str, str],
    objects: list[dict],
    auth_serial: str = MAC,
) -> Mock:
    req = Mock(spec=web.Request)
    req.headers = {"Authorization": _auth_header(auth_serial)}
    req.json = AsyncMock(return_value={"objects": objects})
    req.app = {"state_service": state_service, "mac_to_serial": mac_to_serial}
    return req


async def _put(
    state_service: DeviceStateService,
    mac_to_serial: dict[str, str],
    objects: list[dict],
    auth_serial: str = MAC,
) -> dict:
    req = _make_put_request(state_service, mac_to_serial, objects, auth_serial)
    resp = await handle_transport_put(req)
    assert resp.status == 200
    return json.loads(resp.body)


@pytest.mark.asyncio
async def test_put_resolves_via_in_memory_cache(
    state_service: DeviceStateService,
) -> None:
    """A PUT from a MAC-only device is rewritten to the cached real serial."""
    mac_to_serial = {MAC_LOWER: REAL_SERIAL}

    body = await _put(
        state_service,
        mac_to_serial,
        [{"object_key": f"device.{MAC_LOWER}", "value": {"current_humidity": 70}}],
    )

    assert body["objects"][0]["object_key"] == f"device.{REAL_SERIAL_LOWER}"

    stored = state_service.get_object(REAL_SERIAL, f"device.{REAL_SERIAL_LOWER}")
    assert stored is not None
    assert stored.value == {"current_humidity": 70}

    # Nothing should have been written under the MAC
    assert state_service.get_objects_by_serial(MAC) == []


@pytest.mark.asyncio
async def test_put_resolves_via_persisted_mapping_and_warms_cache(
    state_service: DeviceStateService,
) -> None:
    """If the in-memory cache is empty (e.g. after a restart), PUT falls back
    to the persisted mac_alias mapping and repopulates the cache."""
    await state_service.upsert_object(
        DeviceObject(
            serial=f"mac_alias.{MAC_LOWER}",
            object_key="mac_alias",
            object_revision=1,
            object_timestamp=int(time.time() * 1000),
            value={"serial": REAL_SERIAL},
            updated_at=datetime.now(),
        )
    )

    mac_to_serial: dict[str, str] = {}

    body = await _put(
        state_service,
        mac_to_serial,
        [{"object_key": f"device.{MAC_LOWER}", "value": {"current_humidity": 65}}],
    )

    assert body["objects"][0]["object_key"] == f"device.{REAL_SERIAL_LOWER}"
    assert mac_to_serial[MAC_LOWER] == REAL_SERIAL

    stored = state_service.get_object(REAL_SERIAL, f"device.{REAL_SERIAL_LOWER}")
    assert stored is not None
    assert stored.value == {"current_humidity": 65}


@pytest.mark.asyncio
async def test_put_without_mapping_uses_mac_as_serial(
    state_service: DeviceStateService,
) -> None:
    """With no known mapping, PUT proceeds using the MAC as the serial
    (unchanged from pre-alias behavior)."""
    mac_to_serial: dict[str, str] = {}

    body = await _put(
        state_service,
        mac_to_serial,
        [{"object_key": f"device.{MAC_LOWER}", "value": {"current_humidity": 40}}],
    )

    assert body["objects"][0]["object_key"] == f"device.{MAC_LOWER}"

    stored = state_service.get_object(MAC, f"device.{MAC_LOWER}")
    assert stored is not None
    assert stored.value == {"current_humidity": 40}


# ---------------------------------------------------------------------------
# Outbound: the shared bucket is addressed by MAC, other buckets by serial
# ---------------------------------------------------------------------------


def test_to_device_object_key_maps_shared_bucket_to_mac() -> None:
    """The device only recognises its shared bucket as shared.<mac>."""
    assert to_device_object_key(f"shared.{REAL_SERIAL_LOWER}", MAC_LOWER) == f"shared.{MAC_LOWER}"
    assert to_device_object_key(f"shared.{REAL_SERIAL}", MAC_LOWER) == f"shared.{MAC_LOWER}"


def test_to_device_object_key_leaves_other_buckets_alone() -> None:
    for key in (
        f"device.{REAL_SERIAL_LOWER}",
        f"schedule.{REAL_SERIAL_LOWER}",
        "structure.default",
        "user.1234",
    ):
        assert to_device_object_key(key, MAC_LOWER) == key


def test_to_device_object_key_without_alias_is_noop() -> None:
    key = f"shared.{REAL_SERIAL_LOWER}"
    assert to_device_object_key(key, None) == key


def test_objects_to_device_keys_does_not_mutate_input() -> None:
    objects = [
        {"object_key": f"shared.{REAL_SERIAL_LOWER}", "value": {"target_temperature": 21.5}},
        {"object_key": f"device.{REAL_SERIAL_LOWER}", "value": {}},
    ]
    translated = objects_to_device_keys(objects, MAC_LOWER)

    assert [o["object_key"] for o in translated] == [
        f"shared.{MAC_LOWER}",
        f"device.{REAL_SERIAL_LOWER}",
    ]
    assert translated[0]["value"] == {"target_temperature": 21.5}
    assert objects[0]["object_key"] == f"shared.{REAL_SERIAL_LOWER}"
    assert objects_to_device_keys(objects, None) is objects


@pytest.mark.asyncio
async def test_subscribe_replies_with_shared_bucket_keyed_by_mac(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
) -> None:
    """A device subscribing to shared.<mac> must get the bucket back under that
    same key, otherwise it never tracks the bucket and ignores later pushes."""
    await state_service.upsert_object(
        DeviceObject(
            serial=REAL_SERIAL,
            object_key=f"shared.{REAL_SERIAL_LOWER}",
            object_revision=7,
            object_timestamp=2000,
            value={"target_temperature": 21.5},
            updated_at=datetime.now(),
        )
    )

    body = await _subscribe(
        state_service,
        subscription_manager,
        {},
        {
            "chunked": False,
            "session": MAC_LOWER + REAL_SERIAL,
            "objects": [
                {"object_key": f"shared.{MAC_LOWER}", "object_revision": 0, "object_timestamp": 0}
            ],
        },
    )

    assert [o["object_key"] for o in body["objects"]] == [f"shared.{MAC_LOWER}"]
    assert body["objects"][0]["object_revision"] == 7
    assert body["objects"][0]["value"] == {"target_temperature": 21.5}

    # Server-side storage stays keyed by the real serial
    assert state_service.get_object(REAL_SERIAL, f"shared.{REAL_SERIAL_LOWER}") is not None


@pytest.mark.asyncio
async def test_put_ack_for_shared_bucket_is_keyed_by_mac(
    state_service: DeviceStateService,
) -> None:
    """The PUT acknowledgement carries the revision back under the key the
    device sent, so the device can record it instead of staying at revision 0."""
    mac_to_serial = {MAC_LOWER: REAL_SERIAL}

    body = await _put(
        state_service,
        mac_to_serial,
        [
            {"object_key": f"shared.{MAC_LOWER}", "value": {"current_temperature": 21.0}},
            {"object_key": f"device.{REAL_SERIAL_LOWER}", "value": {"current_humidity": 50}},
        ],
    )

    keys = [o["object_key"] for o in body["objects"]]
    assert keys == [f"shared.{MAC_LOWER}", f"device.{REAL_SERIAL_LOWER}"]

    stored = state_service.get_object(REAL_SERIAL, f"shared.{REAL_SERIAL_LOWER}")
    assert stored is not None
    assert stored.value == {"current_temperature": 21.0}
    assert body["objects"][0]["object_revision"] == stored.object_revision


# ---------------------------------------------------------------------------
# Heartbeat middleware: marks the resolved serial as seen, not the MAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_middleware_resolves_mac_to_real_serial(
    device_availability: DeviceAvailability,
) -> None:
    """The heartbeat middleware should mark the real serial as seen when the
    request's serial is a known MAC alias."""
    middleware = create_device_heartbeat_middleware(device_availability)

    req = Mock(spec=web.Request)
    req.headers = {"Authorization": _auth_header(MAC)}
    req.app = {"mac_to_serial": {MAC_LOWER: REAL_SERIAL}}

    handler = AsyncMock(return_value=web.Response(text="ok"))

    await middleware(req, handler)

    assert device_availability.get_last_seen(REAL_SERIAL) is not None
    assert device_availability.get_last_seen(MAC) is None


@pytest.mark.asyncio
async def test_heartbeat_middleware_resolves_via_persisted_mapping_and_warms_cache(
    state_service: DeviceStateService,
    device_availability: DeviceAvailability,
) -> None:
    """On the first request after a restart, the in-memory mac_to_serial cache
    is empty. The heartbeat middleware should fall back to the persisted
    mac_alias mapping so the heartbeat isn't attributed to the MAC, and warm
    the cache for subsequent requests."""
    await state_service.upsert_object(
        DeviceObject(
            serial=f"mac_alias.{MAC_LOWER}",
            object_key="mac_alias",
            object_revision=1,
            object_timestamp=int(time.time() * 1000),
            value={"serial": REAL_SERIAL},
            updated_at=datetime.now(),
        )
    )

    middleware = create_device_heartbeat_middleware(device_availability)

    mac_to_serial: dict[str, str] = {}
    req = Mock(spec=web.Request)
    req.headers = {"Authorization": _auth_header(MAC)}
    req.app = {"mac_to_serial": mac_to_serial, "state_service": state_service}

    handler = AsyncMock(return_value=web.Response(text="ok"))

    await middleware(req, handler)

    assert device_availability.get_last_seen(REAL_SERIAL) is not None
    assert device_availability.get_last_seen(MAC) is None
    assert mac_to_serial[MAC_LOWER] == REAL_SERIAL


@pytest.mark.asyncio
async def test_heartbeat_middleware_without_mapping_uses_request_serial(
    device_availability: DeviceAvailability,
) -> None:
    """With no MAC mapping known, the heartbeat tracks the serial as-is."""
    middleware = create_device_heartbeat_middleware(device_availability)

    req = Mock(spec=web.Request)
    req.headers = {"Authorization": _auth_header(REAL_SERIAL)}
    req.app = {"mac_to_serial": {}}

    handler = AsyncMock(return_value=web.Response(text="ok"))

    await middleware(req, handler)

    assert device_availability.get_last_seen(REAL_SERIAL) is not None


# ---------------------------------------------------------------------------
# device_auth middleware: resolves MAC aliases before owner/entry-key checks
#
# Devices like Display-2.12 authenticate with their MAC on every request, not
# just the first /subscribe. If device_auth doesn't resolve the alias, a
# device that's actually paired (under its real serial) looks unknown on
# every gated request and gets a 401.
# ---------------------------------------------------------------------------


def _make_device_auth_request(
    state_service: DeviceStateService,
    mac_to_serial: dict[str, str],
    storage: Mock,
    auth_serial: str,
) -> tuple[MagicMock, dict]:
    """Build a mock aiohttp request for device_auth_middleware.

    Returns the request mock plus a plain dict backing request[...] storage,
    since MagicMock's __setitem__ doesn't persist values by default.
    """
    req = MagicMock(spec=web.Request)
    req.headers = {"Authorization": _auth_header(auth_serial)}
    req.path = "/nest/transport"
    req.method = "POST"
    req.app = {
        "state_service": state_service,
        "mac_to_serial": mac_to_serial,
        "storage": storage,
    }
    req_state: dict = {}
    req.__setitem__.side_effect = req_state.__setitem__
    req.__getitem__.side_effect = req_state.__getitem__
    return req, req_state


def _make_storage(
    owner_serial: str | None = None,
    owners: dict[str, str] | None = None,
    entry_key_serial: str | None = None,
) -> Mock:
    """Mock SQLModelService.

    `owner_serial` is paired to "user-1"; `owners` maps further serials to
    user ids. `entry_key_serial` has an active entry key (pending pairing).
    """
    storage = Mock()
    owner_map = dict(owners or {})
    if owner_serial:
        owner_map[owner_serial] = "user-1"

    def get_owner(serial: str) -> DeviceOwner | None:
        user_id = owner_map.get(serial)
        if not user_id:
            return None
        return DeviceOwner(serial=serial, user_id=user_id, created_at=datetime.now())

    def set_owner(owner: DeviceOwner) -> None:
        owner_map[owner.serial] = owner.user_id

    def delete_owner(serial: str, user_id: str) -> bool:
        if owner_map.get(serial) != user_id:
            return False
        del owner_map[serial]
        return True

    storage.owner_map = owner_map
    storage.get_device_owner = AsyncMock(side_effect=get_owner)
    storage.set_device_owner = AsyncMock(side_effect=set_owner)
    storage.delete_device_owner = AsyncMock(side_effect=delete_owner)
    storage.get_entry_key_by_serial = AsyncMock(
        side_effect=lambda serial: (
            Mock() if entry_key_serial and serial == entry_key_serial else None
        )
    )
    return storage


@pytest.mark.asyncio
async def test_device_auth_resolves_mac_via_persisted_mapping(
    state_service: DeviceStateService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device paired under its real serial (and its MAC, same user), but
    authenticating via MAC, must pass auth using the persisted mac_alias mapping (no in-memory cache
    yet — e.g. right after a server restart, before /subscribe runs again)."""
    monkeypatch.setattr(settings, "require_device_pairing", True)

    await state_service.upsert_object(
        DeviceObject(
            serial=f"mac_alias.{MAC_LOWER}",
            object_key="mac_alias",
            object_revision=1,
            object_timestamp=int(time.time() * 1000),
            value={"serial": REAL_SERIAL},
            updated_at=datetime.now(),
        )
    )

    mac_to_serial: dict[str, str] = {}
    storage = _make_storage(owners={REAL_SERIAL: "user-1", MAC: "user-1"})
    req, req_state = _make_device_auth_request(state_service, mac_to_serial, storage, MAC)
    handler = AsyncMock(return_value=web.Response(text="ok"))

    middleware = create_device_auth_middleware()
    resp = await middleware(req, handler)

    handler.assert_awaited_once()
    assert resp.status == 200
    assert req_state["device_serial"] == REAL_SERIAL
    assert req_state["device_auth_tier"] == TIER_PAIRED

    # Cache is warmed so the heartbeat middleware (which runs after this one)
    # also tracks the resolved serial.
    assert mac_to_serial[MAC_LOWER] == REAL_SERIAL


@pytest.mark.asyncio
async def test_device_auth_resolves_mac_via_in_memory_cache(
    state_service: DeviceStateService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the in-memory mapping is warm, paired MAC devices pass auth on
    subsequent requests without a 401."""
    monkeypatch.setattr(settings, "require_device_pairing", True)

    mac_to_serial = {MAC_LOWER: REAL_SERIAL}
    storage = _make_storage(owners={REAL_SERIAL: "user-1", MAC: "user-1"})
    req, req_state = _make_device_auth_request(state_service, mac_to_serial, storage, MAC)
    handler = AsyncMock(return_value=web.Response(text="ok"))

    middleware = create_device_auth_middleware()
    resp = await middleware(req, handler)

    handler.assert_awaited_once()
    assert resp.status == 200
    assert req_state["device_serial"] == REAL_SERIAL
    assert req_state["device_auth_tier"] == TIER_PAIRED


@pytest.mark.asyncio
async def test_device_auth_unmapped_mac_with_no_owner_is_unknown(
    state_service: DeviceStateService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """True first contact (no mapping yet, no owner/entry-key under the MAC)
    is still rejected — resolution can't invent a mapping out of nothing."""
    monkeypatch.setattr(settings, "require_device_pairing", True)

    mac_to_serial: dict[str, str] = {}
    storage = _make_storage(owner_serial=None)
    req, req_state = _make_device_auth_request(state_service, mac_to_serial, storage, MAC)
    handler = AsyncMock(return_value=web.Response(text="ok"))

    middleware = create_device_auth_middleware()
    resp = await middleware(req, handler)

    handler.assert_not_awaited()
    assert resp.status == 401
    assert req_state["device_serial"] == MAC
    assert req_state["device_auth_tier"] == TIER_UNKNOWN


# ---------------------------------------------------------------------------
# Pairing mode: an unauthenticated session ID must not take over another
# user's paired device
# ---------------------------------------------------------------------------


async def _auth_with_alias(
    state_service: DeviceStateService, storage: Mock, path: str = "/nest/transport/put"
) -> tuple[web.StreamResponse, dict, AsyncMock]:
    mac_to_serial = {MAC_LOWER: REAL_SERIAL}
    req, req_state = _make_device_auth_request(state_service, mac_to_serial, storage, MAC)
    req.path = path
    handler = AsyncMock(return_value=web.Response(text="ok"))
    resp = await create_device_auth_middleware()(req, handler)
    return resp, req_state, handler


@pytest.mark.asyncio
async def test_device_auth_rejects_pending_mac_aliased_to_paired_serial(
    state_service: DeviceStateService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A made-up MAC with only an entry key must not get access to a serial
    someone else has paired."""
    monkeypatch.setattr(settings, "require_device_pairing", True)
    storage = _make_storage(owners={REAL_SERIAL: "user-1"}, entry_key_serial=MAC)

    resp, req_state, handler = await _auth_with_alias(state_service, storage)

    handler.assert_not_awaited()
    assert resp.status == 401
    assert req_state["device_auth_tier"] == TIER_UNKNOWN


@pytest.mark.asyncio
async def test_device_auth_rejects_mac_of_other_user_aliased_to_paired_serial(
    state_service: DeviceStateService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "require_device_pairing", True)
    storage = _make_storage(owners={REAL_SERIAL: "user-1", MAC: "user-2"})

    resp, _, handler = await _auth_with_alias(state_service, storage)

    handler.assert_not_awaited()
    assert resp.status == 401


@pytest.mark.asyncio
async def test_device_auth_accepts_device_paired_under_its_mac(
    state_service: DeviceStateService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pairing a MAC-only device records the owner under the MAC; once the
    alias to its (unowned) real serial exists it must still count as paired."""
    monkeypatch.setattr(settings, "require_device_pairing", True)
    storage = _make_storage(owners={MAC: "user-1"})

    resp, req_state, handler = await _auth_with_alias(state_service, storage)

    handler.assert_awaited_once()
    assert resp.status == 200
    assert req_state["device_serial"] == REAL_SERIAL
    assert req_state["device_auth_tier"] == TIER_PAIRED


@pytest.mark.asyncio
async def test_device_auth_keeps_pending_mac_pending_after_alias(
    state_service: DeviceStateService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A MAC-only device in the middle of pairing (entry key under the MAC)
    can still subscribe once aliased to its unowned real serial."""
    monkeypatch.setattr(settings, "require_device_pairing", True)
    storage = _make_storage(entry_key_serial=MAC)

    resp, req_state, handler = await _auth_with_alias(
        state_service, storage, path="/nest/transport/v7/subscribe"
    )

    handler.assert_awaited_once()
    assert resp.status == 200
    assert req_state["device_auth_tier"] == TIER_PENDING


@pytest.mark.asyncio
async def test_subscribe_does_not_alias_mac_to_serial_owned_by_other_user(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "require_device_pairing", True)
    mac_to_serial: dict[str, str] = {}
    body = {"session": f"{MAC_LOWER}{REAL_SERIAL}", "chunked": False, "objects": []}
    req = _make_subscribe_request(state_service, subscription_manager, mac_to_serial, body)
    req.app["storage"] = _make_storage(owners={REAL_SERIAL: "user-1"}, entry_key_serial=MAC)

    resp = await handle_transport_subscribe(req)

    assert resp.status == 200
    assert mac_to_serial == {}
    assert state_service.get_object(f"mac_alias.{MAC_LOWER}", "mac_alias") is None


@pytest.mark.asyncio
async def test_subscribe_aliases_mac_to_unowned_serial_in_pairing_mode(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "require_device_pairing", True)
    mac_to_serial: dict[str, str] = {}
    body = {"session": f"{MAC_LOWER}{REAL_SERIAL}", "chunked": False, "objects": []}
    req = _make_subscribe_request(state_service, subscription_manager, mac_to_serial, body)
    req.app["storage"] = _make_storage(entry_key_serial=MAC)

    resp = await handle_transport_subscribe(req)

    assert resp.status == 200
    assert mac_to_serial == {MAC_LOWER: REAL_SERIAL}


# ---------------------------------------------------------------------------
# Pairing mode: a device paired under its MAC moves its owner record to the
# real serial once the alias is trusted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_device_auth_moves_owner_from_mac_to_unowned_real_serial(
    state_service: DeviceStateService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "require_device_pairing", True)
    storage = _make_storage(owners={MAC: "user-1"})

    resp, req_state, handler = await _auth_with_alias(state_service, storage)

    handler.assert_awaited_once()
    assert resp.status == 200
    assert req_state["device_auth_tier"] == TIER_PAIRED
    assert storage.owner_map == {REAL_SERIAL: "user-1"}


@pytest.mark.asyncio
async def test_device_auth_drops_mac_owner_when_real_serial_has_same_owner(
    state_service: DeviceStateService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "require_device_pairing", True)
    storage = _make_storage(owners={MAC: "user-1", REAL_SERIAL: "user-1"})

    resp, req_state, handler = await _auth_with_alias(state_service, storage)

    handler.assert_awaited_once()
    assert req_state["device_auth_tier"] == TIER_PAIRED
    assert storage.owner_map == {REAL_SERIAL: "user-1"}
    storage.set_device_owner.assert_not_awaited()


@pytest.mark.asyncio
async def test_device_auth_never_overwrites_owner_of_real_serial(
    state_service: DeviceStateService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real serial belongs to someone else: the request is refused and
    neither owner record is touched."""
    monkeypatch.setattr(settings, "require_device_pairing", True)
    storage = _make_storage(owners={MAC: "user-2", REAL_SERIAL: "user-1"})

    resp, _, handler = await _auth_with_alias(state_service, storage)

    handler.assert_not_awaited()
    assert resp.status == 401
    assert storage.owner_map == {MAC: "user-2", REAL_SERIAL: "user-1"}
    storage.set_device_owner.assert_not_awaited()
    storage.delete_device_owner.assert_not_awaited()


@pytest.mark.asyncio
async def test_device_auth_pending_mac_keeps_entry_key_and_no_owner(
    state_service: DeviceStateService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "require_device_pairing", True)
    storage = _make_storage(entry_key_serial=MAC)

    resp, req_state, handler = await _auth_with_alias(
        state_service, storage, path="/nest/transport/v7/subscribe"
    )

    handler.assert_awaited_once()
    assert req_state["device_auth_tier"] == TIER_PENDING
    assert storage.owner_map == {}
    storage.set_device_owner.assert_not_awaited()


@pytest.mark.asyncio
async def test_subscribe_moves_owner_from_mac_to_real_serial_in_pairing_mode(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "require_device_pairing", True)
    mac_to_serial: dict[str, str] = {}
    body = {"session": f"{MAC_LOWER}{REAL_SERIAL}", "chunked": False, "objects": []}
    req = _make_subscribe_request(state_service, subscription_manager, mac_to_serial, body)
    storage = _make_storage(owners={MAC: "user-1"})
    req.app["storage"] = storage

    resp = await handle_transport_subscribe(req)

    assert resp.status == 200
    assert mac_to_serial == {MAC_LOWER: REAL_SERIAL}
    assert storage.owner_map == {REAL_SERIAL: "user-1"}


@pytest.mark.asyncio
async def test_subscribe_leaves_owners_alone_when_real_serial_owned_by_other_user(
    state_service: DeviceStateService,
    subscription_manager: SubscriptionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "require_device_pairing", True)
    mac_to_serial: dict[str, str] = {}
    body = {"session": f"{MAC_LOWER}{REAL_SERIAL}", "chunked": False, "objects": []}
    req = _make_subscribe_request(state_service, subscription_manager, mac_to_serial, body)
    storage = _make_storage(owners={MAC: "user-2", REAL_SERIAL: "user-1"})
    req.app["storage"] = storage

    resp = await handle_transport_subscribe(req)

    assert resp.status == 200
    assert mac_to_serial == {}
    assert storage.owner_map == {MAC: "user-2", REAL_SERIAL: "user-1"}
    storage.set_device_owner.assert_not_awaited()
    storage.delete_device_owner.assert_not_awaited()
