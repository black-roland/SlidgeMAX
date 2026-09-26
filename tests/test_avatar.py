from __future__ import annotations

import logging
from types import SimpleNamespace

from slidge.util.types import Avatar

from slidgemax.contact import Contact


class _Task:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _Session:
    def __init__(self) -> None:
        self.client = None
        self.scheduled: list[tuple[object, str]] = []
        self.tasks: list[_Task] = []

    def create_task(self, coro: object, *, name: str) -> _Task:
        self.scheduled.append((coro, name))
        task = _Task()
        self.tasks.append(task)
        return task

    def cached_presence(self, ident: int) -> None:
        return None


def _contact(*, cached: str | None = None) -> tuple[Contact, _Session, list[Avatar | None]]:
    session = _Session()
    contact = Contact.__new__(Contact)
    contact.session = session  # type: ignore[assignment]
    contact.stored = SimpleNamespace(
        id=1,
        jid="7@gateway",
        avatar=None if cached is None else SimpleNamespace(legacy_id=cached, url=None),
        legacy_id="7",
        nick="Ada",
    )
    contact._set_avatar_task = None
    contact.log = logging.getLogger("test")
    applied: list[Avatar | None] = []

    async def set_avatar(avatar: Avatar | None = None, delete: bool = False) -> None:
        applied.append(avatar)

    contact.set_avatar = set_avatar  # type: ignore[method-assign]
    return contact, session, applied


async def test_update_avatar_schedules_url() -> None:
    contact, session, applied = _contact()
    await contact.update_avatar("  https://i.oneme.ru/i?r=tok  ", 42)
    assert applied == []
    assert len(session.scheduled) == 1
    coro, name = session.scheduled[0]
    assert "r=" not in name
    await coro
    assert len(applied) == 1
    avatar = applied[0]
    assert isinstance(avatar, Avatar)
    assert avatar.url == "https://i.oneme.ru/i?r=tok"
    assert avatar.unique_id == "42"
    assert avatar.data is None
    assert "&fn=" not in (avatar.url or "")


async def test_unchanged_id_does_not_schedule() -> None:
    contact, session, applied = _contact(cached="42")
    await contact.update_avatar("https://i.oneme.ru/i?r=tok", 42)
    assert session.scheduled == []
    assert applied == []


async def test_missing_avatar_does_not_schedule() -> None:
    contact, session, applied = _contact()
    await contact.update_avatar(None, None)
    assert session.scheduled == []
    assert applied == []


async def test_partial_avatar_does_not_schedule_or_clear() -> None:
    contact, session, applied = _contact(cached="9")
    await contact.update_avatar("https://i.oneme.ru/i?r=tok", None)
    await contact.update_avatar("http://i.oneme.ru/i?r=tok", 3)
    await contact.update_avatar(None, 3)
    assert session.scheduled == []
    assert applied == []


async def test_both_missing_clears_cached_avatar() -> None:
    contact, session, applied = _contact(cached="9")
    await contact.update_avatar(None, None)
    assert len(session.scheduled) == 1
    await session.scheduled[0][0]
    assert applied == [None]


async def test_newer_avatar_cancels_previous_task() -> None:
    contact, session, applied = _contact()
    await contact.update_avatar("https://i.oneme.ru/i?r=old", 1)
    await contact.update_avatar("https://i.oneme.ru/i?r=new", 2)
    assert session.tasks[0].cancelled is True
    session.scheduled[0][0].close()
    await session.scheduled[1][0]
    avatar = applied[0]
    assert isinstance(avatar, Avatar)
    assert avatar.url == "https://i.oneme.ru/i?r=new"
    assert avatar.unique_id == "2"


async def test_update_info_without_user_skips_avatar() -> None:
    contact, _session, _applied = _contact()
    called: list[object] = []

    async def update_avatar(*args: object) -> None:
        called.append(args)

    contact.update_avatar = update_avatar  # type: ignore[method-assign]
    await contact.update_info()
    assert called == []
