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

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from max_transport.auth import AuthAttempt, QueuePasswordProvider, QueueSmsProvider
from max_transport.config import Config
from max_transport.jids import bare_jid, safe_filename
from max_transport.store import RegisteredUser, UserStore
from max_transport.util import (
    display_name,
    int_or_none,
    is_call_attachment,
    is_group_chat,
    looks_like_incoming_call,
    opcode_name,
    payload_as_dict,
    parse_call_info,
    sender_id,
)

log = logging.getLogger("max_transport.session")

IncomingHandler = Callable[..., Awaitable[None]]


def _extra_config(config: Config, token: str | None = None) -> Any:
    from pymax import ExtraConfig

    kwargs: dict[str, Any] = {
        "reconnect": config.max.reconnect,
        "reconnect_delay": config.max.reconnect_delay_seconds,
        "log_level": config.logging.level,
    }
    if token:
        kwargs["token"] = token
    device_type = _device_type(config.max.device_type)
    if device_type is not None:
        kwargs["device_type"] = device_type
    try:
        return ExtraConfig(**kwargs)
    except TypeError:
        kwargs.pop("device_type", None)
        return ExtraConfig(**kwargs)


def _device_type(name: str) -> Any | None:
    try:
        from pymax.api.session.enums import DeviceType
    except Exception:
        return name
    try:
        return DeviceType[name]
    except Exception:
        return None


class MaxSession:
    """One PyMax Client bound to one registered XMPP user."""

    def __init__(
        self,
        config: Config,
        user: RegisteredUser,
        *,
        sms_provider: QueueSmsProvider | None = None,
        password_provider: QueuePasswordProvider | None = None,
    ) -> None:
        self.config = config
        self.user = user
        self.sms_provider = sms_provider
        self.password_provider = password_provider
        self.client: Any = None
        self.work_dir = config.sessions_dir / safe_filename(user.jid)
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._failed = asyncio.Event()
        self._error: str | None = None
        self._on_message: IncomingHandler | None = None
        self._on_edit: IncomingHandler | None = None
        self._on_call: IncomingHandler | None = None
        self._on_ready: IncomingHandler | None = None
        self._pushed_peers: set[int] = set()

    @property
    def jid(self) -> str:
        return bare_jid(self.user.jid)

    @property
    def me_id(self) -> int | None:
        return self.user.max_user_id or self._me_id_from_client()

    def _me_id_from_client(self) -> int | None:
        client = self.client
        if client is None:
            return None
        return int_or_none(getattr(client, "me", None))

    def set_handlers(
        self,
        *,
        on_message: IncomingHandler | None = None,
        on_edit: IncomingHandler | None = None,
        on_call: IncomingHandler | None = None,
        on_ready: IncomingHandler | None = None,
    ) -> None:
        self._on_message = on_message
        self._on_edit = on_edit
        self._on_call = on_call
        self._on_ready = on_ready

    def _make_client(self) -> Any:
        from pymax import Client

        self.work_dir.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {
            "phone": self.user.phone,
            "work_dir": str(self.work_dir),
            "session_name": "session.db",
            "extra_config": _extra_config(self.config),
        }
        if self.sms_provider is not None:
            kwargs["sms_code_provider"] = self.sms_provider
        if self.password_provider is not None:
            kwargs["password_provider"] = self.password_provider
        try:
            return Client(**kwargs)
        except TypeError:
            kwargs.pop("sms_code_provider", None)
            kwargs.pop("password_provider", None)
            client = Client(**kwargs)
            if self.sms_provider is not None:
                try:
                    client.sms_code_provider = self.sms_provider
                except Exception:
                    log.warning("could not attach sms_code_provider; PyMax API may have changed")
            if self.password_provider is not None:
                try:
                    client.password_provider = self.password_provider
                except Exception:
                    log.warning("could not attach password_provider; PyMax API may have changed")
            return client

    def _attach_client_handlers(self, client: Any) -> None:
        @client.on_start()
        async def on_start(c: Any) -> None:
            me_id = int_or_none(getattr(c, "me", None))
            self.user.max_user_id = me_id
            self.user.max_name = display_name(getattr(c, "me", None), fallback=None)
            self._ready.set()
            log.info("MAX session ready for %s (max id=%s)", self.jid, me_id)
            if self._on_ready is not None:
                await self._on_ready(self)

        @client.on_message()
        async def on_message(message: Any, c: Any) -> None:
            if self._on_call is not None:
                for attach in getattr(message, "attaches", None) or []:
                    if is_call_attachment(attach):
                        await self._on_call(self, message, attach)
                        return
            if self._on_message is not None:
                await self._on_message(self, message)

        try:

            @client.on_message_edit()
            async def on_edit(message: Any, c: Any) -> None:
                if self._on_edit is not None:
                    await self._on_edit(self, message)
        except Exception:
            log.debug("on_message_edit is not available")

        @client.on_raw()
        async def on_raw(frame: Any, c: Any) -> None:
            if not self.config.bridge.call_notifications:
                return
            opcode = getattr(frame, "opcode", None)
            payload = payload_as_dict(getattr(frame, "payload", None))
            if looks_like_incoming_call(opcode, payload):
                info = parse_call_info(payload)
                log.info(
                    "incoming MAX call for %s opcode=%s info=%s",
                    self.jid,
                    opcode_name(opcode),
                    info,
                )
                if self._on_call is not None:
                    await self._on_call(self, None, info)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._ready.clear()
        self._failed.clear()
        self._error = None
        self.client = self._make_client()
        self._attach_client_handlers(self.client)
        self._task = asyncio.create_task(self._run(), name=f"max-session:{self.jid}")

    async def _run(self) -> None:
        assert self.client is not None
        try:
            await self.client.start()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = str(exc) or exc.__class__.__name__
            log.exception("MAX session for %s failed: %s", self.jid, exc)
            self._failed.set()
        finally:
            if not self._ready.is_set():
                self._failed.set()

    async def wait_ready(self, timeout: float) -> None:
        remaining = timeout
        loop = asyncio.get_running_loop()
        deadline = loop.time() + remaining
        while True:
            timeout_left = deadline - loop.time()
            if timeout_left <= 0:
                raise TimeoutError("MAX login timed out")
            waiters: list[asyncio.Task[bool]] = [
                asyncio.create_task(self._ready.wait()),
                asyncio.create_task(self._failed.wait()),
            ]
            if self.password_provider is not None:
                waiters.append(asyncio.create_task(self.password_provider.requested.wait()))
            done, pending = await asyncio.wait(waiters, timeout=timeout_left, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if self._failed.is_set() and not self._ready.is_set():
                raise RuntimeError(self._error or "MAX login failed")
            if self._ready.is_set():
                return
            if self.password_provider is not None and self.password_provider.requested.is_set():
                return

    async def stop(self) -> None:
        task = self._task
        client = self.client
        self._task = None
        if task is not None:
            task.cancel()
        if client is not None:
            for method_name in ("stop", "close", "disconnect"):
                method = getattr(client, method_name, None)
                if callable(method):
                    try:
                        result = method()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        log.debug("client.%s failed", method_name, exc_info=True)
                    break
        if task is not None:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self.client = None

    def wipe_session_files(self) -> None:
        if self.work_dir.exists():
            shutil.rmtree(self.work_dir, ignore_errors=True)

    def contacts(self) -> list[Any]:
        client = self.client
        if client is None:
            return []
        return [c for c in (getattr(client, "contacts", None) or []) if c is not None]

    def dialogs(self) -> list[Any]:
        client = self.client
        if client is None:
            return []
        return list(getattr(client, "dialogs", None) or [])

    def chats(self) -> list[Any]:
        client = self.client
        if client is None:
            return []
        return list(getattr(client, "chats", None) or [])

    def find_chat(self, chat_id: int) -> Any | None:
        for chat in self.chats() + self.dialogs():
            if int_or_none(getattr(chat, "id", None)) == chat_id:
                return chat
        getter = getattr(self.client, "get_chat", None)
        return None if getter is None else None

    def resolve_dialog_peer(self, message: Any) -> int | None:
        me_id = self.me_id
        chat_id = int_or_none(getattr(message, "chat_id", None))
        sid = sender_id(message)
        if chat_id is not None:
            chat = self.find_chat(chat_id)
            if chat is not None:
                if self.config.bridge.ignore_groups and is_group_chat(chat):
                    return None
                owner = int_or_none(getattr(chat, "owner", None))
                if owner and owner != me_id:
                    return owner
            if me_id is not None:
                peer = chat_id ^ me_id
                if sid is None:
                    return None if self.config.bridge.ignore_groups else peer
                if sid in {me_id, peer}:
                    return peer
                if self.config.bridge.ignore_groups:
                    return None
        if sid is not None and sid != me_id:
            return sid
        return None

    async def send_text(self, peer_id: int, text: str) -> Any:
        client = self.client
        if client is None:
            raise RuntimeError("MAX session is not connected")
        me_id = self.me_id
        if me_id is None:
            raise RuntimeError("MAX account id is unknown")
        chat_id = me_id ^ peer_id
        return await client.send_message(chat_id=chat_id, text=text)

    async def edit_text(self, chat_id: int, message_id: int, text: str) -> Any:
        client = self.client
        if client is None:
            raise RuntimeError("MAX session is not connected")
        return await client.edit_message(chat_id=chat_id, message_id=message_id, text=text)

    async def lookup_user(self, user_id: int) -> Any | None:
        client = self.client
        if client is None:
            return None
        getter = getattr(client, "get_user", None)
        if getter is None:
            return None
        try:
            return await getter(user_id)
        except Exception:
            log.debug("get_user(%s) failed", user_id, exc_info=True)
            return None

    async def lookup_users(self, user_ids: list[int]) -> dict[int, Any]:
        client = self.client
        if client is None:
            return {}
        getter = getattr(client, "get_users", None)
        if getter is None:
            return {}
        try:
            results = await getter(user_ids)
        except Exception:
            log.debug("get_users(%s) failed", user_ids, exc_info=True)
            return {}
        users: dict[int, Any] = {}
        for user in results or []:
            ident = int_or_none(user)
            if ident is not None:
                users[ident] = user
        return users

    async def search_phone(self, phone: str) -> Any | None:
        client = self.client
        if client is None:
            return None
        search = getattr(client, "search_by_phone", None)
        if search is None:
            return None
        try:
            return await search(phone)
        except Exception:
            log.debug("search_by_phone failed", exc_info=True)
            return None

    async def add_contact(self, user_id: int) -> Any | None:
        client = self.client
        if client is None:
            return None
        add = getattr(client, "add_contact", None)
        if add is None:
            return None
        try:
            return await add(user_id)
        except Exception:
            log.debug("add_contact(%s) failed", user_id, exc_info=True)
            return None

    async def remove_contact(self, user_id: int) -> None:
        client = self.client
        if client is None:
            return
        remove = getattr(client, "remove_contact", None)
        if remove is None:
            return
        try:
            await remove(user_id)
        except Exception:
            log.debug("remove_contact(%s) failed", user_id, exc_info=True)


class SessionManager:
    def __init__(self, config: Config, users: UserStore) -> None:
        self.config = config
        self.users = users
        self._sessions: dict[str, MaxSession] = {}

    def get(self, jid: str) -> MaxSession | None:
        return self._sessions.get(bare_jid(jid))

    def all(self) -> list[MaxSession]:
        return list(self._sessions.values())

    def attach(self, session: MaxSession) -> MaxSession:
        self._sessions[session.jid] = session
        return session

    async def start_user(self, user: RegisteredUser, **kwargs: Any) -> MaxSession:
        existing = self.get(user.jid)
        if existing is not None:
            return existing
        session = MaxSession(self.config, user, **kwargs)
        self.attach(session)
        await session.start()
        return session

    async def stop_user(self, jid: str) -> None:
        session = self._sessions.pop(bare_jid(jid), None)
        if session is not None:
            await session.stop()

    async def unregister(self, jid: str) -> None:
        session = self._sessions.pop(bare_jid(jid), None)
        if session is not None:
            await session.stop()
            session.wipe_session_files()
        self.users.delete(jid)

    async def start_all(self) -> None:
        for user in self.users.list():
            try:
                await self.start_user(user)
            except Exception:
                log.exception("failed to start MAX session for %s", user.jid)

    async def stop_all(self) -> None:
        jids = list(self._sessions)
        for jid in jids:
            await self.stop_user(jid)


def begin_auth_attempt(phone: str) -> AuthAttempt:
    return AuthAttempt(phone=phone)
