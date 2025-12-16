"""Tests for Control API endpoints."""

import time
from datetime import datetime

import httpx
import pytest

from nolongerevil.lib.types import DeviceObject, DeviceOwner, EntryKey, UserInfo
from nolongerevil.services.device_state_service import DeviceStateService
from nolongerevil.services.sqlmodel_service import SQLModelService


class TestWebuiEndpoint:
    """Tests for GET / (webui) endpoint."""

    async def test_webui_returns_html(self, control_client: httpx.AsyncClient) -> None:
        """Test that webui returns HTML content."""
        resp = await control_client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        text = resp.text
        assert "<html" in text.lower() or "<!doctype" in text.lower()

    async def test_webui_injects_ingress_path(self, control_client: httpx.AsyncClient) -> None:
        """Test that X-Ingress-Path header is injected into body tag."""
        resp = await control_client.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/abc123"})
        assert resp.status_code == 200
        text = resp.text
        assert 'data-ingress-path="/api/hassio_ingress/abc123"' in text


class TestCommandEndpoint:
    """Tests for POST /command endpoint."""

    async def test_command_requires_serial(self, control_client: httpx.AsyncClient) -> None:
        """Test that command endpoint requires serial."""
        resp = await control_client.post("/command", json={"command": "set_mode", "value": "heat"})
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False
        assert "Serial required" in data["message"]

    async def test_command_requires_command(self, control_client: httpx.AsyncClient) -> None:
        """Test that command endpoint requires command field."""
        resp = await control_client.post("/command", json={"serial": "ABC123", "value": "heat"})
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False
        assert "Command required" in data["message"]

    async def test_command_rejects_invalid_command(self, control_client: httpx.AsyncClient) -> None:
        """Test that command endpoint rejects invalid commands."""
        resp = await control_client.post(
            "/command", json={"serial": "ABC123", "command": "invalid_command", "value": "test"}
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False
        assert "Unknown command" in data["message"]

    async def test_command_invalid_json(self, control_client: httpx.AsyncClient) -> None:
        """Test that command endpoint handles invalid JSON."""
        resp = await control_client.post(
            "/command", content="not json", headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False
        assert "Invalid JSON" in data["message"]

    async def test_set_mode_command(
        self,
        control_client: httpx.AsyncClient,
        state_service: DeviceStateService,
    ) -> None:
        """Test set_mode command."""
        serial = "TEST123"
        # Create initial device object
        await state_service.upsert_object(
            DeviceObject(
                serial=serial,
                object_key=f"device.{serial}",
                object_revision=1,
                object_timestamp=int(time.time() * 1000),
                value={"target_temperature_type": "off"},
                updated_at=datetime.now(),
            )
        )

        resp = await control_client.post(
            "/command", json={"serial": serial, "command": "set_mode", "value": "heat"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["values"]["target_temperature_type"] == "heat"

    async def test_set_temperature_command(
        self,
        control_client: httpx.AsyncClient,
        state_service: DeviceStateService,
    ) -> None:
        """Test set_temperature command."""
        serial = "TEST123"
        await state_service.upsert_object(
            DeviceObject(
                serial=serial,
                object_key=f"device.{serial}",
                object_revision=1,
                object_timestamp=int(time.time() * 1000),
                value={"target_temperature": 20.0},
                updated_at=datetime.now(),
            )
        )

        resp = await control_client.post(
            "/command", json={"serial": serial, "command": "set_temperature", "value": 22.5}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["values"]["target_temperature"] == 22.5

    async def test_set_fan_on_command(
        self,
        control_client: httpx.AsyncClient,
        state_service: DeviceStateService,
    ) -> None:
        """Test set_fan command with 'on' value."""
        serial = "TEST123"
        await state_service.upsert_object(
            DeviceObject(
                serial=serial,
                object_key=f"device.{serial}",
                object_revision=1,
                object_timestamp=int(time.time() * 1000),
                value={"fan_timer_timeout": 0},
                updated_at=datetime.now(),
            )
        )

        resp = await control_client.post(
            "/command", json={"serial": serial, "command": "set_fan", "value": "on"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        # Fan timer should be set to future timestamp
        assert data["data"]["values"]["fan_timer_timeout"] > int(time.time())


class TestStatusEndpoint:
    """Tests for GET /status endpoint."""

    async def test_status_requires_serial(self, control_client: httpx.AsyncClient) -> None:
        """Test that status endpoint requires serial parameter."""
        resp = await control_client.get("/status")
        assert resp.status_code == 400
        data = resp.json()
        assert "Serial parameter required" in data["error"]

    async def test_status_not_found(self, control_client: httpx.AsyncClient) -> None:
        """Test that status endpoint returns 404 for unknown device."""
        resp = await control_client.get("/status?serial=NONEXISTENT")
        assert resp.status_code == 404
        data = resp.json()
        assert "Device not found" in data["error"]

    async def test_status_returns_device_info(
        self,
        control_client: httpx.AsyncClient,
        state_service: DeviceStateService,
    ) -> None:
        """Test that status endpoint returns device information."""
        serial = "TEST456"
        await state_service.upsert_object(
            DeviceObject(
                serial=serial,
                object_key=f"device.{serial}",
                object_revision=1,
                object_timestamp=int(time.time() * 1000),
                value={
                    "current_temperature": 21.5,
                    "target_temperature_type": "heat",
                    "current_humidity": 45,
                },
                updated_at=datetime.now(),
            )
        )
        await state_service.upsert_object(
            DeviceObject(
                serial=serial,
                object_key=f"shared.{serial}",
                object_revision=1,
                object_timestamp=int(time.time() * 1000),
                value={"target_temperature": 22.0},
                updated_at=datetime.now(),
            )
        )

        resp = await control_client.get(f"/status?serial={serial}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["serial"] == serial
        assert data["humidity"] == 45
        assert data["target_temperature"] == 22.0


class TestDevicesEndpoint:
    """Tests for GET /api/devices endpoint."""

    async def test_devices_empty(self, control_client: httpx.AsyncClient) -> None:
        """Test that devices endpoint returns empty list when no devices."""
        resp = await control_client.get("/api/devices")
        assert resp.status_code == 200
        data = resp.json()
        assert data["devices"] == []
        assert data["total"] == 0

    async def test_devices_returns_list(
        self,
        control_client: httpx.AsyncClient,
        state_service: DeviceStateService,
    ) -> None:
        """Test that devices endpoint returns device list."""
        serial = "TEST789"
        await state_service.upsert_object(
            DeviceObject(
                serial=serial,
                object_key=f"device.{serial}",
                object_revision=1,
                object_timestamp=int(time.time() * 1000),
                value={"model": "Learning Thermostat"},
                updated_at=datetime.now(),
            )
        )

        resp = await control_client.get("/api/devices")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["devices"]) == 1
        assert data["devices"][0]["serial"] == serial


class TestNotifyDeviceEndpoint:
    """Tests for POST /notify-device endpoint."""

    async def test_notify_device_requires_serial(self, control_client: httpx.AsyncClient) -> None:
        """Test that notify-device requires serial."""
        resp = await control_client.post("/notify-device", json={})
        assert resp.status_code == 400
        data = resp.json()
        assert "Serial required" in data["error"]

    async def test_notify_device_not_found(self, control_client: httpx.AsyncClient) -> None:
        """Test that notify-device returns 404 for unknown device."""
        resp = await control_client.post("/notify-device", json={"serial": "NONEXISTENT"})
        assert resp.status_code == 404
        data = resp.json()
        assert "Device not found" in data["error"]

    async def test_notify_device_success(
        self,
        control_client: httpx.AsyncClient,
        state_service: DeviceStateService,
    ) -> None:
        """Test successful device notification."""
        serial = "NOTIFY123"
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

        resp = await control_client.post("/notify-device", json={"serial": serial})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "subscribers_notified" in data


class TestStatsEndpoint:
    """Tests for GET /api/stats endpoint."""

    async def test_stats_returns_data(self, control_client: httpx.AsyncClient) -> None:
        """Test that stats endpoint returns statistics."""
        resp = await control_client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "devices" in data
        assert "subscriptions" in data
        assert "availability" in data


class TestDismissPairingEndpoint:
    """Tests for POST /api/dismiss-pairing/{serial} endpoint."""

    async def test_dismiss_pairing_no_dialog(self, control_client: httpx.AsyncClient) -> None:
        """Test dismiss pairing when no dialog exists."""
        resp = await control_client.post("/api/dismiss-pairing/NODEVICE")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "No pairing dialog to dismiss" in data["message"]

    async def test_dismiss_pairing_with_dialog(
        self,
        control_client: httpx.AsyncClient,
        state_service: DeviceStateService,
    ) -> None:
        """Test dismiss pairing when dialog exists."""
        serial = "PAIRING123"
        # Create the alert dialog
        await state_service.upsert_object(
            DeviceObject(
                serial=serial,
                object_key=f"device_alert_dialog.{serial}",
                object_revision=1,
                object_timestamp=int(time.time() * 1000),
                value={"dialog_id": "confirm-pairing"},
                updated_at=datetime.now(),
            )
        )

        resp = await control_client.post(f"/api/dismiss-pairing/{serial}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "dismissed" in data["message"].lower()


class TestDeleteDeviceEndpoint:
    """Tests for DELETE /api/device endpoint."""

    async def test_delete_device_requires_serial(self, control_client: httpx.AsyncClient) -> None:
        """Test that delete device requires serial."""
        resp = await control_client.request("DELETE", "/api/device", json={})
        assert resp.status_code == 400
        data = resp.json()
        assert "Serial required" in data["error"]

    async def test_delete_device_not_found(self, control_client: httpx.AsyncClient) -> None:
        """Test that delete device returns 404 for unknown device."""
        resp = await control_client.request("DELETE", "/api/device", json={"serial": "NONEXISTENT"})
        assert resp.status_code == 404
        data = resp.json()
        assert "Device not found" in data["error"]

    async def test_delete_device_success(
        self,
        control_client: httpx.AsyncClient,
        state_service: DeviceStateService,
    ) -> None:
        """Test successful device deletion."""
        serial = "DELETE123"
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

        resp = await control_client.request("DELETE", "/api/device", json={"serial": serial})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["objects_deleted"] > 0


class TestRegistrationEndpoints:
    """Tests for registration API endpoints."""

    async def test_register_requires_code_and_user(self, control_client: httpx.AsyncClient) -> None:
        """Test that register requires code and userId."""
        resp = await control_client.post("/api/register", json={})
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False
        assert "Missing required fields" in data["message"]

    async def test_register_validates_code_format(self, control_client: httpx.AsyncClient) -> None:
        """Test that register validates entry code format."""
        resp = await control_client.post(
            "/api/register", json={"code": "INVALID!", "userId": "testuser"}
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False
        assert "Invalid entry code format" in data["message"]

    async def test_register_invalid_code(self, control_client: httpx.AsyncClient) -> None:
        """Test that register rejects non-existent code."""
        resp = await control_client.post(
            "/api/register", json={"code": "ABC1234", "userId": "testuser"}
        )
        # Should return success=false (not a 400) for invalid codes
        data = resp.json()
        assert data["success"] is False
        assert "Invalid" in data["message"] or "expired" in data["message"]

    async def test_register_success(
        self,
        control_client: httpx.AsyncClient,
        sqlmodel_service: SQLModelService,
    ) -> None:
        """Test successful device registration."""
        # Create a valid entry key
        serial = "REG123"
        code = "ABC1234"
        entry_key = EntryKey(
            code=code,
            serial=serial,
            created_at=datetime.now(),
            expires_at=datetime.now().replace(year=2030),
            claimed_by=None,
            claimed_at=None,
        )
        await sqlmodel_service.create_entry_key(entry_key)

        # Create user
        user = UserInfo(clerk_id="testuser", email="test@test.com", created_at=datetime.now())
        await sqlmodel_service.create_user(user)

        resp = await control_client.post(
            "/api/register", json={"code": code, "userId": "testuser"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["serial"] == serial

    async def test_registered_devices_empty(self, control_client: httpx.AsyncClient) -> None:
        """Test that registered-devices returns empty list for new user."""
        resp = await control_client.get("/api/registered-devices?userId=newuser")
        assert resp.status_code == 200
        data = resp.json()
        assert data == []

    async def test_registered_devices_with_devices(
        self,
        control_client: httpx.AsyncClient,
        sqlmodel_service: SQLModelService,
    ) -> None:
        """Test that registered-devices returns device list."""
        user_id = "regtest"
        serial = "REGDEV123"

        # Create user and device owner
        user = UserInfo(clerk_id=user_id, email="reg@test.com", created_at=datetime.now())
        await sqlmodel_service.create_user(user)

        owner = DeviceOwner(serial=serial, user_id=user_id, created_at=datetime.now())
        await sqlmodel_service.set_device_owner(owner)

        resp = await control_client.get(f"/api/registered-devices?userId={user_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["serial"] == serial

    async def test_delete_registered_device(
        self,
        control_client: httpx.AsyncClient,
        sqlmodel_service: SQLModelService,
    ) -> None:
        """Test deleting a registered device."""
        user_id = "deltest"
        serial = "DELDEV123"

        user = UserInfo(clerk_id=user_id, email="del@test.com", created_at=datetime.now())
        await sqlmodel_service.create_user(user)

        owner = DeviceOwner(serial=serial, user_id=user_id, created_at=datetime.now())
        await sqlmodel_service.set_device_owner(owner)

        resp = await control_client.request(
            "DELETE", f"/api/registered-devices/{serial}?userId={user_id}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    async def test_ensure_user_creates_new(self, control_client: httpx.AsyncClient) -> None:
        """Test that ensure-user creates new user."""
        resp = await control_client.post(
            "/api/ensure-user", json={"userId": "newuser123", "email": "new@test.com"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["created"] is True

    async def test_ensure_user_existing(
        self,
        control_client: httpx.AsyncClient,
        sqlmodel_service: SQLModelService,
    ) -> None:
        """Test that ensure-user returns existing user."""
        user_id = "existinguser"
        user = UserInfo(clerk_id=user_id, email="exist@test.com", created_at=datetime.now())
        await sqlmodel_service.create_user(user)

        resp = await control_client.post("/api/ensure-user", json={"userId": user_id})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["created"] is False

    async def test_mqtt_config_requires_broker_url(self, control_client: httpx.AsyncClient) -> None:
        """Test that mqtt-config requires brokerUrl."""
        resp = await control_client.post("/api/mqtt-config", json={})
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False
        assert "brokerUrl" in data["message"]

    async def test_mqtt_config_success(
        self,
        control_client: httpx.AsyncClient,
        sqlmodel_service: SQLModelService,
    ) -> None:
        """Test successful MQTT config."""
        # Ensure homeassistant user exists
        user = UserInfo(clerk_id="homeassistant", email="ha@local", created_at=datetime.now())
        await sqlmodel_service.create_user(user)

        resp = await control_client.post(
            "/api/mqtt-config",
            json={
                "brokerUrl": "mqtt://localhost:1883",
                "topicPrefix": "nest",
                "discoveryPrefix": "homeassistant",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
