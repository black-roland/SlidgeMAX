import asyncio

import pytest

from slidgemax.auth import QueuePasswordProvider, QueueSmsProvider


@pytest.mark.asyncio
async def test_sms_provider():
    p = QueueSmsProvider()
    await p.set_code("  123 456 ")
    code = await p.get_code("+7")
    assert code == "123 456"


@pytest.mark.asyncio
async def test_password_provider():
    p = QueuePasswordProvider()
    # simulate MAX asking
    fut = asyncio.create_task(p.get_password("hint-42"))
    await asyncio.sleep(0)  # let it set requested
    assert p.requested.is_set()
    assert p.hint == "hint-42"
    await p.set_password("secret")
    pw = await fut
    assert pw == "secret"
