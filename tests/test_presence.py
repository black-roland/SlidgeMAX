from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

from pymax.protocol.enums import Opcode
from pymax.types.domain.presence import Presence

from slidgemax.session import Session
from slidgemax.util import contact_presence_entries, map_presence, presence_seen


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
