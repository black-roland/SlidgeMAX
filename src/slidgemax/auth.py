from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("slidgemax.auth")


class QueueSmsProvider:
    """SmsCodeProvider that waits for a Slidge 2FA prompt to supply the code."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    async def set_code(self, code: str) -> None:
        await self._queue.put(code.strip())

    async def get_code(self, phone: str) -> str:
        log.info("waiting for SMS code for %s", phone)
        return await self._queue.get()


class QueuePasswordProvider:
    """PasswordProvider that surfaces a 2FA prompt to the XMPP user."""

    def __init__(self, on_request: Callable[[str | None], Awaitable[None] | None] | None = None) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self.hint: str | None = None
        self.requested = asyncio.Event()
        self.requests = 0
        self._on_request = on_request

    async def set_password(self, password: str) -> None:
        await self._queue.put(password)

    async def get_password(self, hint: str | None = None) -> str:
        self.hint = hint
        self.requests += 1
        self.requested.set()
        log.info("MAX requested 2FA password (hint=%s)", hint)
        if self._on_request is not None:
            result = self._on_request(hint)
            if asyncio.iscoroutine(result):
                await result
        return await self._queue.get()
