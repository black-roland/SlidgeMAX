from __future__ import annotations

from datetime import UTC, datetime

from slidgemax.util import map_presence, presence_seen


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
