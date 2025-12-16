"""Tests for Nest API endpoints."""

import time
from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx

from nolongerevil.lib.types import DeviceObject
from nolongerevil.services.device_state_service import DeviceStateService


class TestPingEndpoint:
    """Tests for GET /nest/ping endpoint."""

    async def test_ping_returns_ok(self, nest_client: httpx.AsyncClient) -> None:
        """Test that ping returns ok status."""
        resp = await nest_client.get("/nest/ping")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "timestamp" in data
        assert isinstance(data["timestamp"], int)


class TestEntryEndpoint:
    """Tests for /nest/entry endpoint."""

    async def test_entry_get(self, nest_client: httpx.AsyncClient) -> None:
        """Test GET /nest/entry returns service URLs."""
        resp = await nest_client.get("/nest/entry")
        assert resp.status_code == 200
        data = resp.json()
        assert "czfe_url" in data
        assert "transport_url" in data
        assert "passphrase_url" in data
        assert "weather_url" in data
        assert "upload_url" in data
        assert data["server_version"] == "1.0.0"

    async def test_entry_post(self, nest_client: httpx.AsyncClient) -> None:
        """Test POST /nest/entry returns service URLs."""
        resp = await nest_client.post("/nest/entry")
        assert resp.status_code == 200
        data = resp.json()
        assert "transport_url" in data


class TestProInfoEndpoint:
    """Tests for GET /nest/pro_info/{code} endpoint."""

    async def test_pro_info_returns_data(self, nest_client: httpx.AsyncClient) -> None:
        """Test that pro_info returns installer data."""
        resp = await nest_client.get("/nest/pro_info/ABC123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pro_id"] == "ABC123"
        assert data["company_name"] == "Self-Hosted"


class TestUploadEndpoint:
    """Tests for POST /nest/upload endpoint."""

    async def test_upload_accepts_data(self, nest_client: httpx.AsyncClient) -> None:
        """Test that upload endpoint accepts data."""
        resp = await nest_client.post(
            "/nest/upload",
            content=b"test log data",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestPassphraseEndpoint:
    """Tests for /nest/passphrase endpoints."""

    async def test_passphrase_requires_serial(self, nest_client: httpx.AsyncClient) -> None:
        """Test that passphrase requires device serial."""
        resp = await nest_client.get("/nest/passphrase")
        assert resp.status_code == 400
        data = resp.json()
        assert "Device serial required" in data["error"]

    async def test_passphrase_generates_code(
        self,
        nest_client: httpx.AsyncClient,
        state_service: DeviceStateService,
    ) -> None:
        """Test that passphrase generates entry code."""
        serial = "PASS12345678"  # Must be at least 10 chars
        # Create device object to identify the device
        await state_service.upsert_object(
            DeviceObject(
                serial=serial,
                object_key=f"device.{serial}",
                object_revision=1,
                object_timestamp=int(time.time() * 1000),
                value={},
                updated_at=datetime.now(),
            )
        )

        resp = await nest_client.get(
            "/nest/passphrase",
            headers={"X-NL-Device-Serial": serial},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "value" in data  # entry code
        assert "expires" in data

    async def test_passphrase_status_no_key(self, nest_client: httpx.AsyncClient) -> None:
        """Test passphrase status when no key exists."""
        resp = await nest_client.get(
            "/nest/passphrase/status",
            headers={"X-NL-Device-Serial": "NOKEY1234567"},  # Must be at least 10 chars
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "no_key"
        assert data["claimed"] is False


class TestTransportEndpoints:
    """Tests for /nest/transport endpoints."""

    async def test_transport_get_requires_serial(self, nest_client: httpx.AsyncClient) -> None:
        """Test that transport GET requires serial."""
        resp = await nest_client.get("/nest/transport/device/")
        # Will match the catch-all route, but still needs serial
        assert resp.status_code in (400, 404)

    async def test_transport_get_device_objects(
        self,
        nest_client: httpx.AsyncClient,
        state_service: DeviceStateService,
    ) -> None:
        """Test transport GET returns device objects."""
        serial = "TRANS123"
        await state_service.upsert_object(
            DeviceObject(
                serial=serial,
                object_key=f"device.{serial}",
                object_revision=5,
                object_timestamp=int(time.time() * 1000),
                value={"test": "data"},
                updated_at=datetime.now(),
            )
        )

        resp = await nest_client.get(f"/nest/transport/device/{serial}")
        assert resp.status_code == 200
        data = resp.json()
        assert "objects" in data
        # Should have at least the device object and alert dialog
        assert len(data["objects"]) >= 1

    async def test_transport_subscribe_requires_serial(self, nest_client: httpx.AsyncClient) -> None:
        """Test that transport subscribe requires serial."""
        resp = await nest_client.post("/nest/transport", json={"objects": []})
        assert resp.status_code == 400
        data = resp.json()
        assert "Device serial required" in data["error"]

    async def test_transport_subscribe_invalid_json(self, nest_client: httpx.AsyncClient) -> None:
        """Test transport subscribe with invalid JSON."""
        resp = await nest_client.post(
            "/nest/transport",
            content="not json",
            headers={
                "Content-Type": "application/json",
                "X-NL-Device-Serial": "TEST12345678",  # Must be at least 10 chars
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "Invalid JSON" in data["error"]

    async def test_transport_subscribe_returns_objects(
        self,
        nest_client: httpx.AsyncClient,
        state_service: DeviceStateService,
    ) -> None:
        """Test transport subscribe returns server objects."""
        serial = "SUB123456789"  # Must be at least 10 chars
        now_ms = int(time.time() * 1000)

        # Create server state
        await state_service.upsert_object(
            DeviceObject(
                serial=serial,
                object_key=f"device.{serial}",
                object_revision=10,
                object_timestamp=now_ms,
                value={"temperature": 22.5},
                updated_at=datetime.now(),
            )
        )

        # Client requests with older revision (should get server's data)
        resp = await nest_client.post(
            "/nest/transport",
            json={
                "objects": [
                    {
                        "object_key": f"device.{serial}",
                        "object_revision": 0,
                        "object_timestamp": 0,
                    }
                ],
                "chunked": False,
            },
            headers={"X-NL-Device-Serial": serial},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "objects" in data
        assert len(data["objects"]) >= 1
        # Server should return its object with value
        obj = data["objects"][0]
        assert obj["object_key"] == f"device.{serial}"
        assert obj["value"]["temperature"] == 22.5

    async def test_transport_subscribe_device_update(
        self,
        nest_client: httpx.AsyncClient,
        state_service: DeviceStateService,
    ) -> None:
        """Test transport subscribe handles device updates."""
        serial = "UPDATE12345678"  # Must be at least 10 chars

        # Device sends update (rev=0, ts=0 means update)
        resp = await nest_client.post(
            "/nest/transport",
            json={
                "objects": [
                    {
                        "object_key": f"device.{serial}",
                        "object_revision": 0,
                        "object_timestamp": 0,
                        "value": {"current_temperature": 21.0},
                    }
                ],
                "chunked": False,
            },
            headers={"X-NL-Device-Serial": serial},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "objects" in data

        # Verify state was saved
        obj = state_service.get_object(serial, f"device.{serial}")
        assert obj is not None
        assert obj.value.get("current_temperature") == 21.0

    async def test_transport_put_requires_serial(self, nest_client: httpx.AsyncClient) -> None:
        """Test that transport PUT requires serial."""
        resp = await nest_client.post("/nest/transport/put", json={"objects": []})
        assert resp.status_code == 400

    async def test_transport_put_updates_state(
        self,
        nest_client: httpx.AsyncClient,
        state_service: DeviceStateService,
    ) -> None:
        """Test transport PUT updates device state."""
        serial = "PUT1234567890"  # Must be at least 10 chars

        resp = await nest_client.post(
            "/nest/transport/put",
            json={
                "objects": [
                    {
                        "object_key": f"shared.{serial}",
                        "value": {"target_temperature": 23.0},
                    }
                ]
            },
            headers={"X-NL-Device-Serial": serial},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "objects" in data

        # Verify state was saved
        obj = state_service.get_object(serial, f"shared.{serial}")
        assert obj is not None
        assert obj.value.get("target_temperature") == 23.0

    async def test_transport_versioned_subscribe(
        self,
        nest_client: httpx.AsyncClient,
    ) -> None:
        """Test versioned transport subscribe endpoint."""
        serial = "VER1234567890"  # Must be at least 10 chars
        resp = await nest_client.post(
            "/nest/transport/v7/subscribe",
            json={"objects": [], "chunked": False},
            headers={"X-NL-Device-Serial": serial},
        )
        assert resp.status_code == 200

    async def test_transport_versioned_put(
        self,
        nest_client: httpx.AsyncClient,
    ) -> None:
        """Test versioned transport PUT endpoint."""
        serial = "VERPUT123456"  # Must be at least 10 chars
        resp = await nest_client.post(
            "/nest/transport/v7/put",
            json={
                "objects": [
                    {
                        "object_key": f"device.{serial}",
                        "value": {"test": "value"},
                    }
                ]
            },
            headers={"X-NL-Device-Serial": serial},
        )
        assert resp.status_code == 200


class TestWeatherEndpoint:
    """Tests for /nest/weather endpoints."""

    async def test_weather_v1_endpoint(self, nest_client: httpx.AsyncClient) -> None:
        """Test weather v1 endpoint."""
        # Weather service may return error if no external service configured
        resp = await nest_client.get("/nest/weather/v1?postal_code=12345&country=US")
        # Accept either success or service unavailable
        assert resp.status_code in (200, 502)

    async def test_weather_catch_all(self, nest_client: httpx.AsyncClient) -> None:
        """Test weather catch-all endpoint."""
        resp = await nest_client.get("/nest/weather/v2/forecast")
        # Accept either success or service unavailable
        assert resp.status_code in (200, 502)

    async def test_weather_with_mock(self, nest_client: httpx.AsyncClient) -> None:
        """Test weather endpoint with mocked service."""
        mock_weather = {
            "current": {
                "temp_c": 20.0,
                "humidity": 50,
                "condition": "sunny",
            }
        }

        # Access the weather service from the app state
        app = nest_client._app  # type: ignore[attr-defined]
        weather_service = app.state.weather_service

        # Patch the weather service's get_weather method
        with patch.object(
            weather_service,
            "get_weather",
            new=AsyncMock(return_value=mock_weather),
        ):
            resp = await nest_client.get("/nest/weather/v1?postal_code=12345")
            assert resp.status_code == 200
            data = resp.json()
            assert data["current"]["temp_c"] == 20.0


class TestTransportLegacyPaths:
    """Tests for legacy transport paths."""

    async def test_legacy_czfe_path(
        self,
        nest_client: httpx.AsyncClient,
        state_service: DeviceStateService,
    ) -> None:
        """Test legacy czfe path handling."""
        serial = "LEGACY123"
        await state_service.upsert_object(
            DeviceObject(
                serial=serial,
                object_key=f"device.{serial}",
                object_revision=1,
                object_timestamp=int(time.time() * 1000),
                value={},
                updated_at=datetime.now(),
            )
        )

        # Legacy path format
        resp = await nest_client.get(
            f"/nest/transport/v7/device/device.{serial}",
            headers={"X-nl-weave-device-serial": serial},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "objects" in data
