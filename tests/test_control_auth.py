"""Control panel login (CONTROL_AUTH=true)."""

import pytest

from nolongerevil.config import settings
from nolongerevil.main import create_control_app
from nolongerevil.services.control_auth import hash_password, verify_password

PASSWORD = "correct horse"


@pytest.fixture
async def app(
    monkeypatch, state_service, subscription_manager, device_availability, sqlmodel_service
):
    monkeypatch.setattr(settings, "control_auth", True)
    return create_control_app(
        state_service, subscription_manager, device_availability, sqlmodel_service
    )


@pytest.fixture
async def client(aiohttp_client, app):
    return await aiohttp_client(app)


async def sign_in(client, remember=True):
    await client.app["control_auth"].set_password(PASSWORD)
    r = await client.post("/login", json={"password": PASSWORD, "remember": remember})
    assert r.status == 200
    return r


def test_password_hash_round_trip():
    stored = hash_password(PASSWORD)
    assert verify_password(PASSWORD, stored)
    assert not verify_password("wrong password", stored)
    assert not verify_password(PASSWORD, "not a hash")


async def test_requires_login(client):
    r = await client.get("/", headers={"Accept": "text/html"}, allow_redirects=False)
    assert r.status == 302 and r.headers["Location"] == "/login"
    assert (await client.get("/api/devices")).status == 401
    assert (await client.post("/command", json={})).status == 401
    assert (await client.get("/health")).status == 200
    assert (await client.get("/login")).status == 200


async def test_setup_code_sets_password_once(client):
    code = client.app["control_auth"].new_code("setup")
    r = await client.post("/setup", json={"code": code, "password": "short"})
    assert r.status == 400  # The code is not used up by a rejected password
    r = await client.post("/setup", json={"code": code, "password": PASSWORD, "remember": True})
    assert r.status == 200
    assert (await client.get("/api/devices")).status == 200
    r = await client.post("/setup", json={"code": code, "password": "another password"})
    assert r.status == 409


async def test_login_sets_a_protected_cookie(client):
    r = await sign_in(client)
    cookie = r.cookies["nle_session"]
    assert cookie["httponly"] and cookie["samesite"] == "Strict"
    assert int(cookie["max-age"]) > 300 * 24 * 3600
    assert (await client.get("/api/devices")).status == 200


async def test_session_cookie_without_remember(client):
    r = await sign_in(client, remember=False)
    assert not r.cookies["nle_session"]["max-age"]


async def test_wrong_password_and_lockout(client, monkeypatch):
    auth = client.app["control_auth"]
    await auth.set_password(PASSWORD)
    record = auth.record_failure
    monkeypatch.setattr(auth, "record_failure", lambda c: record(c) and 0)
    for _ in range(10):
        assert (await client.post("/login", json={"password": "wrong"})).status == 401
    assert (await client.post("/login", json={"password": PASSWORD})).status == 429


async def test_cross_site_post_is_refused(client):
    await sign_in(client)
    r = await client.post("/command", json={}, headers={"Origin": "http://evil.example"})
    assert r.status == 403


async def test_api_key_access(client, aiohttp_client, app):
    await sign_in(client)
    key = (await (await client.post("/api/auth/api-keys", json={"name": "script"})).json())["key"]
    other = await aiohttp_client(app)
    assert (
        await other.get("/api/devices", headers={"Authorization": f"Bearer {key}"})
    ).status == 200
    bad = await other.get("/api/devices", headers={"Authorization": "Bearer nlapi_wrong"})
    assert bad.status == 401


async def test_device_link_signs_in_another_browser_once(client, aiohttp_client, app):
    await sign_in(client)
    url = (await (await client.post("/api/auth/device-link")).json())["url"]
    path = url[url.index("/link") :]
    phone = await aiohttp_client(app)
    r = await phone.get(path, allow_redirects=False)
    assert r.status == 302 and r.headers["Location"] == "/"
    assert (await phone.get("/api/devices")).status == 200
    again = await (await aiohttp_client(app)).get(path, allow_redirects=False)
    assert again.headers["Location"] == "/login"


async def test_sign_out_other_devices(client, aiohttp_client, app):
    await sign_in(client)
    phone = await aiohttp_client(app)
    assert (await phone.post("/login", json={"password": PASSWORD})).status == 200
    sessions = await (await client.get("/api/auth/sessions")).json()
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    assert (await client.post("/api/auth/sessions/sign-out-others")).status == 200
    assert (await phone.get("/api/devices")).status == 401
    assert (await client.get("/api/devices")).status == 200


async def test_ingress_header_alone_is_not_trusted(client, monkeypatch):
    headers = {"X-Ingress-Path": "/api/hassio_ingress/x"}
    assert (await client.get("/api/devices", headers=headers)).status == 401
    monkeypatch.setattr(settings, "control_trusted_ingress_ip", "127.0.0.1")
    assert (await client.get("/api/devices", headers=headers)).status == 200


async def test_login_off_by_default(
    aiohttp_client, state_service, subscription_manager, device_availability, sqlmodel_service
):
    app = create_control_app(
        state_service, subscription_manager, device_availability, sqlmodel_service
    )
    client = await aiohttp_client(app)
    assert (await client.get("/api/devices")).status == 200
    assert (await client.get("/login")).status == 404
