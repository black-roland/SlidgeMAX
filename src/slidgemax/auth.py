# Copyright 2026 @black-roland
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""SMS / 2FA providers that wait for Slidge registration forms."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

log = logging.getLogger(__name__)

WaitState = Literal["ready", "sms", "password"]


class QueueSmsProvider:
    """SmsCodeProvider that waits for :meth:`Gateway.validate_two_factor_code`."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self.requested = asyncio.Event()

    async def set_code(self, code: str) -> None:
        await self._queue.put(code.strip())

    async def get_code(self, phone: str) -> str:
        self.requested.set()
        log.info("Waiting for SMS code for %s", phone)
        return await self._queue.get()


class QueuePasswordProvider:
    """PasswordProvider that waits for the optional 2FA password from the registration form."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self.hint: str | None = None
        self.requested = asyncio.Event()

    async def set_password(self, password: str) -> None:
        await self._queue.put(password)

    async def get_password(self, hint: str | None = None) -> str:
        self.hint = hint
        self.requested.set()
        log.info("MAX requested 2FA password (hint=%s)", hint)
        return await self._queue.get()


@dataclass
class PendingAuth:
    """One in-flight MAX login (phone → SMS → optional 2FA)."""

    phone: str
    password: str = ""
    sms: QueueSmsProvider = field(default_factory=QueueSmsProvider)
    password_provider: QueuePasswordProvider = field(default_factory=QueuePasswordProvider)
    client: Any = None
    task: asyncio.Task[None] | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    failed: asyncio.Event = field(default_factory=asyncio.Event)
    error: str | None = None
    me_id: int | None = None
    me_name: str | None = None
    work_dir: str = ""
    created_session: bool = False

    async def wait(
        self,
        timeout: float,
        *,
        ignore: frozenset[str] = frozenset(),
    ) -> WaitState:
        """Wait until ready, failed, SMS requested, or 2FA password requested.

        Returns one of ``"ready"``, ``"sms"``, ``"password"``.
        ``ignore`` skips events that were already consumed (typically ``sms``
        after the code has been submitted).
        """
        events: dict[str, asyncio.Event] = {
            "ready": self.ready,
            "failed": self.failed,
            "sms": self.sms.requested,
            "password": self.password_provider.requested,
        }
        for name in ignore:
            events.pop(name, None)

        waiters = [asyncio.create_task(event.wait()) for event in events.values()]
        done, pending = await asyncio.wait(
            waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()

        if self.failed.is_set() and not self.ready.is_set():
            raise RuntimeError(self.error or "MAX login failed")
        if self.ready.is_set():
            return "ready"
        if "password" not in ignore and self.password_provider.requested.is_set():
            return "password"
        if "sms" not in ignore and self.sms.requested.is_set():
            return "sms"
        if not done:
            raise TimeoutError("MAX login timed out")
        raise RuntimeError("MAX login ended in an unexpected state")
