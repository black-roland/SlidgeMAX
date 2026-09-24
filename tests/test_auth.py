from __future__ import annotations

import asyncio

import pytest

from slidgemax.auth import PendingAuth, QueuePasswordProvider, QueueSmsProvider


@pytest.mark.asyncio
async def test_sms_provider_roundtrip() -> None:
    provider = QueueSmsProvider()
    task = asyncio.create_task(provider.get_code("+7999"))
    await asyncio.sleep(0)
    assert provider.requested.is_set()
    await provider.set_code(" 12345 ")
    assert await task == "12345"


@pytest.mark.asyncio
async def test_password_provider_roundtrip() -> None:
    provider = QueuePasswordProvider()
    task = asyncio.create_task(provider.get_password("hint"))
    await asyncio.sleep(0)
    assert provider.requested.is_set()
    assert provider.hint == "hint"
    await provider.set_password("secret")
    assert await task == "secret"


@pytest.mark.asyncio
async def test_pending_wait_ready() -> None:
    pending = PendingAuth(phone="+7999")
    pending.ready.set()
    assert await pending.wait(0.1) == "ready"


@pytest.mark.asyncio
async def test_pending_wait_sms_then_ready() -> None:
    pending = PendingAuth(phone="+7999")
    pending.sms.requested.set()
    assert await pending.wait(0.1) == "sms"
    pending.ready.set()
    assert await pending.wait(0.1, ignore=frozenset({"sms"})) == "ready"


@pytest.mark.asyncio
async def test_pending_wait_failed() -> None:
    pending = PendingAuth(phone="+7999", error="boom")
    pending.failed.set()
    with pytest.raises(RuntimeError, match="boom"):
        await pending.wait(0.1)


@pytest.mark.asyncio
async def test_pending_wait_timeout() -> None:
    pending = PendingAuth(phone="+7999")
    with pytest.raises(TimeoutError):
        await pending.wait(0.01)
