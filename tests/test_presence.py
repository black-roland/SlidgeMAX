from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pymax.protocol.enums import Opcode
from pymax.types.domain.presence import Presence

from slidgemax import config
from slidgemax.session import Session
from slidgemax.util import contact_presence_entries, map_presence, presence_seen, xmpp_show_online


def test_online_with_seen_seconds() -> None:
    seen = 1781354531
    assert map_presence(1, seen) == ("online", datetime.fromtimestamp(seen, tz=UTC))


def test_seen_only_is_away() -> None:
    seen = 1781360047
    assert map_presence(None, seen) == ("away", datetime.fromtimestamp(seen, tz=UTC))


def test_seen_milliseconds() -> None:
    seconds = 1781354531
    assert presence_seen(seconds * 1000) == datetime.fromtimestamp(seconds, tz=UTC)
    assert map_presence(1, seconds * 1000) == (
        "online",
        datetime.fromtimestamp(seconds, tz=UTC),
    )


def test_seen_not_positive_is_dropped() -> None:
    assert presence_seen(0) is None
    assert presence_seen(-1) is None
    assert map_presence(1, 0) == ("online", None)
    assert map_presence(None, 0) is None


def test_unknown_status_with_seen_is_away() -> None:
    seen = 1781354531
    assert map_presence(2, seen) == ("away", datetime.fromtimestamp(seen, tz=UTC))


def test_unknown_status_without_seen_is_ignored() -> None:
    assert map_presence(2, None) is None


def test_empty_event_is_ignored() -> None:
    assert map_presence(None, None) is None


def test_contact_presence_map() -> None:
    assert contact_presence_entries(
        {"presence": {"11111111": {"seen": 1755700379}, "12": {"seen": 1, "status": 1}}}
    ) == [(11111111, None, 1755700379), (12, 1, 1)]


def test_contact_presence_ignores_login_payload() -> None:
    assert contact_presence_entries({"contacts": [{"id": 1}], "chats": []}) == []
    assert contact_presence_entries({"presence": [{"userId": 1}]}) == []
    assert contact_presence_entries(None) == []


async def test_refresh_presence_keeps_online_over_seen_only() -> None:
    class _Response:
        payload = {"presence": {"10": {"seen": 1755700379}, "20": {"seen": 1755700400, "status": 1}}}

    class _App:
        def __init__(self) -> None:
            self.calls: list[tuple[object, object]] = []

        async def invoke(self, opcode: object, payload: object) -> _Response:
            self.calls.append((opcode, payload))
            return _Response()

    app = _App()
    session = Session.__new__(Session)
    session._presence = {10: Presence(status=1, seen=1)}
    session._unknown_presence = set()
    session._me_id = 1
    session.log = logging.getLogger("test-presence")
    session.client = SimpleNamespace(_app=app)

    await session.refresh_presence([10, 1, 10, 20])

    assert app.calls == [
        (Opcode.CONTACT_PRESENCE, {"contactIds": [10, 20]}),
    ]
    assert session.cached_presence(10) == Presence(status=1, seen=1755700379)
    assert session.cached_presence(20) == Presence(status=1, seen=1755700400)


def test_xmpp_show_online() -> None:
    assert xmpp_show_online("") is True
    assert xmpp_show_online("chat") is True
    assert xmpp_show_online("away") is False
    assert xmpp_show_online("xa") is False
    assert xmpp_show_online("dnd") is False
    assert xmpp_show_online("available") is False


class _PresenceClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[bool] = []
        self.fail = fail

    def set_presence(self, *, online: bool) -> None:
        if self.fail:
            raise RuntimeError("set_presence failed")
        self.calls.append(online)


def _outbound_session(client: object | None) -> Session:
    session = Session.__new__(Session)
    session.client = client  # type: ignore[assignment]
    session._max_online = None
    session.log = logging.getLogger("test-presence")
    return session


async def _send(session: Session, show: str | None, status: str = "") -> None:
    merged = None if show is None else {"show": show, "status": status, "priority": 0}
    await session.on_presence("phone", "", status, {}, merged)


async def test_on_presence_maps_merged_show() -> None:
    client = _PresenceClient()
    session = _outbound_session(client)

    await _send(session, "")
    await _send(session, "chat")
    assert client.calls == [True]

    session._max_online = None
    await _send(session, "away")
    session._max_online = None
    await _send(session, "xa")
    session._max_online = None
    await _send(session, "dnd")
    session._max_online = None
    await _send(session, None)
    assert client.calls == [True, False, False, False, False]


async def test_on_presence_skips_unchanged_and_status_only() -> None:
    client = _PresenceClient()
    session = _outbound_session(client)

    await _send(session, "", status="one")
    await _send(session, "", status="two")
    await _send(session, "chat", status="three")
    assert client.calls == [True]

    await _send(session, "away")
    await _send(session, "dnd", status="busy")
    assert client.calls == [True, False]

    await _send(session, "chat")
    assert client.calls == [True, False, True]


async def test_on_presence_disabled_or_disconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _PresenceClient()
    session = _outbound_session(client)
    monkeypatch.setattr(config, "PRESENCE", False)
    await _send(session, "")
    assert client.calls == []
    assert session._max_online is None

    monkeypatch.setattr(config, "PRESENCE", True)
    disconnected = _outbound_session(None)
    await _send(disconnected, "away")
    assert disconnected._max_online is None


async def test_on_presence_retries_after_set_presence_failure() -> None:
    client = _PresenceClient(fail=True)
    session = _outbound_session(client)
    await _send(session, "")
    assert session._max_online is None

    client.fail = False
    await _send(session, "")
    assert client.calls == [True]
    assert session._max_online is True


async def test_publish_online_makes_following_available_a_noop() -> None:
    client = _PresenceClient()
    session = _outbound_session(client)
    session._publish_online(True)
    await _send(session, "chat", status="here")
    assert client.calls == [True]
    assert session._max_online is True
