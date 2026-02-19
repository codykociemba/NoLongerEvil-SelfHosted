"""Tests for handle_transport_put — guards against stale-echo regressions.

Four bugs were fixed in this handler over 3 days (2026-02-09 through 2026-02-11),
all variations of the server echoing stale bucket data back to the device. These
tests cover the invariants that broke.
"""

import json
import time
from base64 import b64encode
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web

from nolongerevil.lib.types import DeviceObject
from nolongerevil.routes.nest.transport import handle_transport_put
from nolongerevil.services.device_state_service import DeviceStateService

SERIAL = "02AA01AB501203EQ"
AUTH_HEADER = "Basic " + b64encode(f"{SERIAL}:password".encode()).decode()

ALLOWED_RESPONSE_KEYS = {"object_revision", "object_timestamp", "object_key"}


def _make_request(state_service: DeviceStateService, objects: list[dict]) -> Mock:
    """Build a mock aiohttp request for handle_transport_put."""
    req = Mock(spec=web.Request)
    req.headers = {"Authorization": AUTH_HEADER}
    req.json = AsyncMock(return_value={"objects": objects})
    req.app = {"state_service": state_service}
    return req


async def _put(state_service: DeviceStateService, objects: list[dict]) -> dict:
    """Call handle_transport_put and return the parsed response body."""
    req = _make_request(state_service, objects)
    resp = await handle_transport_put(req)
    assert resp.status == 200
    return json.loads(resp.body)


# ---------------------------------------------------------------------------
# 1. Core invariant: PUT responses must not echo bucket values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_response_has_no_value_field(
    state_service: DeviceStateService,
) -> None:
    """Response objects contain only rev/ts/key — never 'value'."""
    body = await _put(
        state_service,
        [
            {
                "object_key": f"device.{SERIAL}",
                "value": {"current_humidity": 45, "target_temperature": 21.0},
            }
        ],
    )

    for obj in body["objects"]:
        assert set(obj.keys()) == ALLOWED_RESPONSE_KEYS
        assert "value" not in obj


# ---------------------------------------------------------------------------
# 2. CAS conflict responses also must not echo values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cas_conflict_response_has_no_value_field(
    state_service: DeviceStateService,
) -> None:
    """A CAS-rejected bucket still returns only rev/ts/key — no value echo."""
    # Pre-populate shared bucket at revision 5
    await state_service.upsert_object(
        DeviceObject(
            serial=SERIAL,
            object_key=f"shared.{SERIAL}",
            object_revision=5,
            object_timestamp=int(time.time() * 1000),
            value={"target_temperature": 22.0},
            updated_at=datetime.now(),
        )
    )

    body = await _put(
        state_service,
        [
            {
                "object_key": f"shared.{SERIAL}",
                "if_object_revision": 3,  # stale — server is at 5
                "value": {"target_temperature": 23.0},
            }
        ],
    )

    assert len(body["objects"]) == 1
    obj = body["objects"][0]
    assert set(obj.keys()) == ALLOWED_RESPONSE_KEYS
    assert obj["object_revision"] == 5  # server's current, not client's


# ---------------------------------------------------------------------------
# 3. CAS conflict on one bucket must not abort remaining buckets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cas_conflict_does_not_abort_remaining_buckets(
    state_service: DeviceStateService,
) -> None:
    """A CAS failure on the shared bucket should not prevent the device bucket
    from being processed in the same request."""
    # Pre-populate shared bucket at revision 5
    await state_service.upsert_object(
        DeviceObject(
            serial=SERIAL,
            object_key=f"shared.{SERIAL}",
            object_revision=5,
            object_timestamp=int(time.time() * 1000),
            value={"target_temperature": 22.0},
            updated_at=datetime.now(),
        )
    )

    body = await _put(
        state_service,
        [
            {
                "object_key": f"shared.{SERIAL}",
                "if_object_revision": 3,  # conflict
                "value": {"target_temperature": 23.0},
            },
            {
                "object_key": f"device.{SERIAL}",
                "value": {"current_humidity": 50},
            },
        ],
    )

    assert len(body["objects"]) == 2

    keys = {obj["object_key"] for obj in body["objects"]}
    assert f"shared.{SERIAL}" in keys
    assert f"device.{SERIAL}" in keys

    # Shared bucket should reflect the conflict (unchanged rev)
    shared = next(o for o in body["objects"] if o["object_key"] == f"shared.{SERIAL}")
    assert shared["object_revision"] == 5

    # Device bucket should have been processed normally
    device = next(o for o in body["objects"] if o["object_key"] == f"device.{SERIAL}")
    assert device["object_revision"] == 1


# ---------------------------------------------------------------------------
# 4. Duplicate PUTs must not bump revision or timestamp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revision_unchanged_on_duplicate_put(
    state_service: DeviceStateService,
) -> None:
    """Sending identical values twice should not bump revision or timestamp."""
    payload = [
        {
            "object_key": f"shared.{SERIAL}",
            "value": {"target_temperature": 21.5},
        }
    ]

    first = await _put(state_service, payload)
    second = await _put(state_service, payload)

    first_obj = first["objects"][0]
    second_obj = second["objects"][0]

    assert second_obj["object_revision"] == first_obj["object_revision"]
    assert second_obj["object_timestamp"] == first_obj["object_timestamp"]


# ---------------------------------------------------------------------------
# 5. Changed values must bump revision and timestamp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revision_increments_on_value_change(
    state_service: DeviceStateService,
) -> None:
    """Changing a value should increment revision and update timestamp."""
    first = await _put(
        state_service,
        [{"object_key": f"shared.{SERIAL}", "value": {"target_temperature": 21.0}}],
    )
    second = await _put(
        state_service,
        [{"object_key": f"shared.{SERIAL}", "value": {"target_temperature": 23.0}}],
    )

    first_obj = first["objects"][0]
    second_obj = second["objects"][0]

    assert second_obj["object_revision"] == first_obj["object_revision"] + 1
    assert second_obj["object_timestamp"] >= first_obj["object_timestamp"]


# ---------------------------------------------------------------------------
# 6. PUT must not piggyback unrelated buckets onto the response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_piggyback_of_shared_bucket(
    state_service: DeviceStateService,
) -> None:
    """PUTting only the device bucket must not drag shared bucket into response."""
    # Pre-populate shared bucket so there's something to piggyback
    await state_service.upsert_object(
        DeviceObject(
            serial=SERIAL,
            object_key=f"shared.{SERIAL}",
            object_revision=3,
            object_timestamp=int(time.time() * 1000),
            value={"target_temperature": 22.0},
            updated_at=datetime.now(),
        )
    )

    body = await _put(
        state_service,
        [{"object_key": f"device.{SERIAL}", "value": {"current_humidity": 45}}],
    )

    assert len(body["objects"]) == 1
    assert body["objects"][0]["object_key"] == f"device.{SERIAL}"


# ---------------------------------------------------------------------------
# 7. PUT must not notify long-poll subscribers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_does_not_access_subscription_manager(
    state_service: DeviceStateService,
) -> None:
    """PUT must not touch subscription_manager — pushing to the subscribe channel
    after a PUT caused stale-value races when TCP delivery was delayed past a
    schedule transition."""
    spy_app: dict = {"state_service": state_service}

    req = Mock(spec=web.Request)
    req.headers = {"Authorization": AUTH_HEADER}
    req.json = AsyncMock(
        return_value={
            "objects": [{"object_key": f"device.{SERIAL}", "value": {"current_humidity": 45}}]
        },
    )
    req.app = spy_app

    resp = await handle_transport_put(req)
    assert resp.status == 200

    # If the handler tried to access request.app["subscription_manager"],
    # it would KeyError on our plain dict.  Reaching here means it didn't.
    assert "subscription_manager" not in spy_app
