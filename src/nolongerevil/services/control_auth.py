"""Control panel login: one password, remembered browser sessions, one-time codes."""

import base64
import hashlib
import hmac
import secrets
import time

from sqlmodel import col, delete, select

from nolongerevil.models import ControlCredentialModel, ControlSessionModel
from nolongerevil.services.sqlmodel_service import SQLModelService

MIN_PASSWORD_LENGTH = 8
REMEMBER_MS = 365 * 24 * 3600 * 1000
SESSION_MS = 12 * 3600 * 1000
TOUCH_MS = 10 * 60 * 1000  # Write last-seen at most this often
SETUP_CODE_SECONDS = 3600
LINK_CODE_SECONDS = 600
MAX_FAILURES = 10
LOCKOUT_SECONDS = 900
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


def _now_ms() -> int:
    return int(time.time() * 1000)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, key = stored.split("$")
        expected = base64.b64decode(key)
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class ControlAuth:
    """Password, sessions and one-time codes for the control panel."""

    def __init__(self, storage: SQLModelService) -> None:
        self._storage = storage
        self._codes: dict[str, tuple[str, float]] = {}  # code -> (purpose, expiry)
        self._failures: dict[str, tuple[int, float]] = {}  # client -> (count, locked until)

    # Password

    async def has_password(self) -> bool:
        async with self._storage._session_maker() as session:
            return await session.get(ControlCredentialModel, 1) is not None

    async def set_password(self, password: str) -> None:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
        async with self._storage._session_maker() as session:
            row = await session.get(ControlCredentialModel, 1)
            if row is None:
                row = ControlCredentialModel(id=1, passwordHash="", updatedAt=0)
            row.passwordHash = hash_password(password)
            row.updatedAt = _now_ms()
            session.add(row)
            await session.commit()

    async def check_password(self, password: str) -> bool:
        async with self._storage._session_maker() as session:
            row = await session.get(ControlCredentialModel, 1)
        return row is not None and verify_password(password, row.passwordHash)

    # One-time codes ("setup" for the first password, "link" to sign in another device)

    def new_code(self, purpose: str) -> str:
        ttl = SETUP_CODE_SECONDS if purpose == "setup" else LINK_CODE_SECONDS
        code = secrets.token_urlsafe(12)
        self._codes[code] = (purpose, time.monotonic() + ttl)
        return code

    def use_code(self, code: str, purpose: str) -> bool:
        entry = self._codes.get(code)
        if entry is None or entry[0] != purpose or entry[1] < time.monotonic():
            return False
        del self._codes[code]
        return True

    # Sessions

    async def create_session(self, name: str, remember: bool) -> str:
        token = secrets.token_urlsafe(32)
        now = _now_ms()
        async with self._storage._session_maker() as session:
            session.add(
                ControlSessionModel(
                    tokenHash=_token_hash(token),
                    name=name[:80],
                    remember=remember,
                    createdAt=now,
                    lastSeenAt=now,
                    expiresAt=now + (REMEMBER_MS if remember else SESSION_MS),
                )
            )
            await session.commit()
        return token

    async def get_session(self, token: str | None) -> ControlSessionModel | None:
        """Return the session for a cookie token, extending it when used."""
        if not token:
            return None
        now = _now_ms()
        async with self._storage._session_maker() as session:
            result = await session.execute(
                select(ControlSessionModel).where(
                    ControlSessionModel.tokenHash == _token_hash(token)
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            if row.expiresAt < now:
                await session.delete(row)
                await session.commit()
                return None
            if now - row.lastSeenAt > TOUCH_MS:
                row.lastSeenAt = now
                row.expiresAt = now + (REMEMBER_MS if row.remember else SESSION_MS)
                await session.commit()
            return row

    async def list_sessions(self) -> list[ControlSessionModel]:
        async with self._storage._session_maker() as session:
            result = await session.execute(
                select(ControlSessionModel).order_by(col(ControlSessionModel.lastSeenAt).desc())
            )
            return list(result.scalars().all())

    async def delete_sessions(self, ids: list[int] | None = None, keep: int | None = None) -> None:
        """Delete the given sessions, or all sessions except `keep`."""
        stmt = delete(ControlSessionModel)
        if ids is not None:
            stmt = stmt.where(col(ControlSessionModel.id).in_(ids))
        if keep is not None:
            stmt = stmt.where(col(ControlSessionModel.id) != keep)
        async with self._storage._session_maker() as session:
            await session.execute(stmt)
            await session.commit()

    # Login throttling, per client address

    def locked_for(self, client: str) -> int:
        count, until = self._failures.get(client, (0, 0.0))
        return max(0, int(until - time.monotonic())) if count >= MAX_FAILURES else 0

    def record_failure(self, client: str) -> int:
        """Record a failed attempt and return the delay (seconds) to apply."""
        count = self._failures.get(client, (0, 0.0))[0] + 1
        self._failures[client] = (count, time.monotonic() + LOCKOUT_SECONDS)
        return min(count, 5)

    def record_success(self, client: str) -> None:
        self._failures.pop(client, None)
