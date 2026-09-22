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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from slidge import BaseSession
from slidge.core import config as slidge_config

if TYPE_CHECKING:
    from .gateway import Gateway


# Dummy MUC/bookmark types since GROUPS=False on the gateway.
# We still need concrete classes for the BaseSession generic derivation.
class _DummyMUC:
    pass


from slidge.group.bookmarks import LegacyBookmarks as _BaseBookmarks  # noqa: E402

class _DummyBookmarks(_BaseBookmarks[_DummyMUC]):  # type: ignore[type-arg]
    pass

from .contact import Roster
from .util import (
    dialog_chat_id,
    int_or_none,
    session_dirname,
    is_call_attachment,
    is_text_attachment_only,
    looks_like_incoming_call,
    parse_call_info,
    payload_as_dict,
    sender_id,
)

log = logging.getLogger("slidgemax.session")


def _extra_config() -> Any:
    from pymax import ExtraConfig

    # Slidge sessions are long-lived; let PyMax reconnect.
    return ExtraConfig(reconnect=True, reconnect_delay=30.0)


class Session(BaseSession[Roster, _DummyBookmarks]):  # type: ignore[type-arg]
    """
    One PyMax Client per registered XMPP user.
    """

    xmpp: "Gateway"  # type: ignore[name-defined]

    def __init__(self, user: Any) -> None:
        super().__init__(user)
        self.client: Any = None
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._me_id: int | None = None
        self._pushed: set[int] = set()

        # Where PyMax stores its session sqlite
        safe = session_dirname(str(user.jid.bare))
        self.work_dir: Path = Path(slidge_config.HOME_DIR) / "max-sessions" / safe
        self.work_dir.mkdir(parents=True, exist_ok=True)

    @property
    def me_id(self) -> int | None:
        if self._me_id is not None:
            return self._me_id
        if self.client is not None:
            self._me_id = int_or_none(self.client.me)
        return self._me_id

    async def login(self) -> str | None:
        """
        Restore / create PyMax Client from stored legacy_module_data (phone) and session files.
        """
        phone = None
        data = self.user.legacy_module_data or {}
        if isinstance(data, dict):
            phone = data.get("phone")
        if not phone:
            # legacy fallback
            phone = data.get("username") if isinstance(data, dict) else None
        if not phone:
            raise RuntimeError("No phone in user data; re-register")

        # If we have a previous max_user_id, keep it
        if isinstance(data, dict) and data.get("max_user_id"):
            self._me_id = int_or_none(data.get("max_user_id"))

        log.info(
            "login jid=%s phone=%s work_dir=%s session_db_exists=%s",
            self.user.jid.bare,
            phone,
            self.work_dir,
            (self.work_dir / "session.db").is_file(),
        )
        self.client = self._make_client(phone)
        self._attach_handlers(self.client)
        self._task = asyncio.create_task(self._run_client(), name=f"slidgemax-{self.user.jid.bare}")

        # Wait until connected or failed (but return promptly; listeners run in bg)
        # Slidge recommends returning once "logged in" or starting the listener task.
        # We wait a short time so that initial roster fill etc can happen.
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass

        me = self.me_id or "?"
        return f"Connected as MAX {me}"

    def _make_client(self, phone: str) -> Any:
        from pymax import Client

        kwargs: dict[str, Any] = {
            "phone": phone,
            "work_dir": str(self.work_dir),
            "session_name": "session.db",
            "extra_config": _extra_config(),
        }
        # No providers here: this is a restored session login. If token missing, PyMax will re-SMS but we have no provider -> will fail.
        # For re-auth, user should unregister+register.
        return Client(**kwargs)

    def _attach_handlers(self, client: Any) -> None:
        @client.on_start()
        async def _on_start(c: Any) -> None:
            self._me_id = int_or_none(c.me)
            self._ready.set()
            log.info("MAX connected for %s (id=%s)", self.user.jid.bare, self._me_id)

        @client.on_message()
        async def _on_msg(message: Any, c: Any) -> None:
            await self._handle_incoming(message)

        @client.on_message_edit()
        async def _on_edit(message: Any, c: Any) -> None:
            await self._handle_incoming_edit(message)

        @client.on_raw()
        async def _on_raw(frame: Any, c: Any) -> None:
            if not self.xmpp.call_notifications:
                return
            op = frame.opcode
            pl = payload_as_dict(frame.payload)
            if looks_like_incoming_call(op, pl):
                info = parse_call_info(pl)
                await self._notify_call(info, None)

    async def _run_client(self) -> None:
        assert self.client is not None
        try:
            await self.client.start()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("MAX client error for %s: %s", self.user.jid.bare, exc)

    async def _handle_incoming(self, message: Any) -> None:
        me = self.me_id
        sid = sender_id(message)
        if me is not None and sid == me:
            return  # echo of own message

        peer = self._resolve_peer(message)
        if peer is None:
            return

        # ensure contact is known
        contact = await self.contacts.by_legacy_id(str(peer))
        if peer not in self._pushed:
            await contact.update_info()
            self._pushed.add(peer)

        body = message.text
        if not body:
            if self.xmpp.placeholder_unsupported and not is_text_attachment_only(message):
                body = self.xmpp.unsupported_text
            else:
                return

        # call detection via attachment
        for att in message.attaches:
            if is_call_attachment(att):
                await self._notify_call({}, message)
                return

        contact.send_text(str(body), legacy_msg_id=str(message.id))

    async def _handle_incoming_edit(self, message: Any) -> None:
        me = self.me_id
        sid = sender_id(message)
        if me is not None and sid == me:
            return
        peer = self._resolve_peer(message)
        if peer is None:
            return
        contact = await self.contacts.by_legacy_id(str(peer))
        contact.correct(str(message.id), message.text or "")

    def _resolve_peer(self, message: Any) -> int | None:
        me = self.me_id
        chat_id = message.chat_id
        sid = sender_id(message)
        if chat_id is not None and me is not None:
            # 1:1 chat id is me ^ peer
            peer = chat_id ^ me
            # verify
            if sid in (me, peer, None):
                return peer
            # group? ignore for now (GROUPS=False)
            return None
        if sid is not None and sid != me:
            return sid
        return None

    async def _notify_call(self, info: dict[str, Any], message: Any | None) -> None:
        if not self.xmpp.call_notifications:
            return
        caller = int_or_none(info.get("caller_id"))
        if caller is None and message is not None:
            caller = self._resolve_peer(message) or sender_id(message)
        if caller is None:
            return
        text = "Incoming video call" if info.get("video") else "Incoming voice call"
        name = info.get("name")
        if name:
            text = f"{text} ({name})"
        contact = await self.contacts.by_legacy_id(str(caller))
        contact.send_text(text)

    async def send_text(self, peer_id: int, text: str) -> Any:
        client = self.client
        if client is None:
            raise RuntimeError("Not connected to MAX")
        me = self.me_id
        if me is None:
            raise RuntimeError("MAX id unknown")
        chat_id = dialog_chat_id(me, peer_id)
        sent = await client.send_message(chat_id=chat_id, text=text)
        return sent

    async def edit_text(self, chat_id: int, message_id: int, text: str) -> Any:
        client = self.client
        if client is None:
            raise RuntimeError("Not connected to MAX")
        return await client.edit_message(chat_id=chat_id, message_id=message_id, text=text)

    # --- roster / contacts helpers used by Roster ---

    def contacts_list(self) -> list[Any]:
        c = self.client
        if c is None:
            return []
        return [x for x in c.contacts if x]

    def chats_list(self) -> list[Any]:
        c = self.client
        if c is None:
            return []
        return list(c.chats or [])

    async def lookup_user(self, user_id: int) -> Any | None:
        c = self.client
        if c is None:
            return None
        try:
            return await c.get_user(user_id)
        except Exception:
            return None

    # shutdown
    async def logout(self) -> None:
        task = self._task
        client = self.client
        self._task = None
        self.client = None
        if task:
            task.cancel()
        if client:
            try:
                await client.stop()
            except Exception:
                log.debug("failed to stop MAX client", exc_info=True)
        if task:
            try:
                await task
            except Exception:
                pass
