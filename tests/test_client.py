from __future__ import annotations

from pathlib import Path

import pytest
from pymax import ExtraConfig
from pymax.exceptions import ApiError
from pymax.protocol.enums import Opcode
from pymax.session.models import SessionInfo
from pymax.session.store import SessionStore

from slidgemax.client import (
    REVOKED_LOGIN_MESSAGE,
    LoginTokenRevoked,
    MaxClient,
    RefuseSmsAuth,
    discard_saved_session,
    is_revoked_login_token,
    start_once_or_recover,
)
from slidgemax.session import make_client


def _revoked() -> ApiError:
    return ApiError(
        opcode=Opcode.LOGIN,
        error="FAIL_LOGIN_TOKEN",
        message="login token was revoked",
    )


def test_revoked_login_token_detector() -> None:
    assert is_revoked_login_token(_revoked())
    assert is_revoked_login_token(
        ApiError(opcode=Opcode.LOGIN, error="FAIL_LOGOUT_ALL", message="logged out")
    )
    assert not is_revoked_login_token(
        ApiError(opcode=Opcode.LOGIN, error="error.limit.violate", message="too many")
    )
    assert not is_revoked_login_token(RuntimeError("FAIL_LOGIN_TOKEN"))


async def test_refuse_sms_auth_does_not_touch_the_client() -> None:
    class Boom:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(name)

    with pytest.raises(LoginTokenRevoked, match="will not request an SMS code"):
        await RefuseSmsAuth().authenticate(Boom())  # type: ignore[arg-type]


def test_unattended_client_cannot_request_sms(tmp_path: Path) -> None:
    client = make_client("+79990000000", tmp_path)
    assert isinstance(client, MaxClient)
    assert isinstance(client._auth_flow, RefuseSmsAuth)
    assert client.extra_config.relogin is False


def test_registration_client_keeps_sms_provider(tmp_path: Path) -> None:
    from slidgemax.auth import QueueSmsProvider

    provider = QueueSmsProvider()
    client = make_client("+79990000000", tmp_path, sms_provider=provider)
    assert not isinstance(client._auth_flow, RefuseSmsAuth)
    assert client._auth_flow.code_provider is provider


class _Harness(MaxClient):
    def __init__(self, tmp_path: Path, *, reconnect: bool) -> None:
        super().__init__(
            "+79990000000",
            work_dir=str(tmp_path),
            extra_config=ExtraConfig(
                reconnect=reconnect,
                reconnect_delay=0,
                relogin=True,
            ),
        )
        self.connect_calls = 0
        self.closed = 0
        self.resets = 0
        self._open = False
        self._fail: BaseException | None = _revoked()

    @property
    def is_connected(self) -> bool:
        return self._open

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._fail is not None:
            raise self._fail
        self._open = True

    async def close(self) -> None:
        self.closed += 1
        self._open = False

    def _reset_runtime(self) -> None:
        self.resets += 1

    async def relogin(self, drop_config_token: bool = True, start: bool = True) -> None:
        raise AssertionError("relogin must not be called")


async def test_start_does_not_retry_revoked_token(tmp_path: Path) -> None:
    client = _Harness(tmp_path, reconnect=True)
    with pytest.raises(LoginTokenRevoked, match=REVOKED_LOGIN_MESSAGE):
        await client.start()
    assert client.connect_calls == 1
    assert client.resets == 0


async def test_start_reconnects_network_errors_only(tmp_path: Path) -> None:
    client = _Harness(tmp_path, reconnect=True)

    async def connect() -> None:
        client.connect_calls += 1
        if client.connect_calls == 1:
            raise ConnectionError("dropped")
        client._open = True

    async def wait_closed(_conn: object) -> None:
        return None

    client.connect = connect  # type: ignore[method-assign]
    client._connection = type("Conn", (), {"wait_closed": wait_closed})()

    await client.start()
    assert client.connect_calls == 2
    assert client.resets == 1


async def test_start_does_not_reconnect_when_disabled(tmp_path: Path) -> None:
    client = _Harness(tmp_path, reconnect=False)
    client._fail = ConnectionError("dropped")
    with pytest.raises(ConnectionError):
        await client.start()
    assert client.connect_calls == 1
    assert client.resets == 0


async def test_registration_recovery_is_one_shot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    discarded: list[object] = []

    async def discard(client: object) -> None:
        discarded.append(client)

    monkeypatch.setattr("slidgemax.client.discard_saved_session", discard)
    client = _Harness(tmp_path, reconnect=True)

    with pytest.raises(LoginTokenRevoked):
        await start_once_or_recover(client, lambda: True)
    assert client.connect_calls == 2
    assert discarded == [client]

    discarded.clear()
    client.connect_calls = 0
    with pytest.raises(LoginTokenRevoked):
        await start_once_or_recover(client, lambda: False)
    assert client.connect_calls == 1
    assert discarded == []


async def test_discard_saved_session_removes_token(tmp_path: Path) -> None:
    client = MaxClient("+79990000000", work_dir=str(tmp_path), session_name="session.db")
    store = SessionStore(str(tmp_path), "session.db")
    await store.save_session(SessionInfo(token="dead-token", device_id="device", phone="+79990000000"))
    await store.close()

    await discard_saved_session(client)

    store = SessionStore(str(tmp_path), "session.db")
    try:
        assert await store.load_session() is None
    finally:
        await store.close()
