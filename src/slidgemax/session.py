from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from slidge import BaseSession
from slidge.core import config as slidge_config

if TYPE_CHECKING:
    from slidge.group.bookmarks import LegacyBookmarks

    from .gateway import Gateway


# Dummy MUC/bookmark types since GROUPS=False on the gateway.
# We still need concrete classes for the BaseSession generic derivation.
class _DummyMUC:
    pass


from slidge.group.bookmarks import LegacyBookmarks as _BaseBookmarks  # noqa: E402

class _DummyBookmarks(_BaseBookmarks[_DummyMUC]):  # type: ignore[type-arg]
    pass

from .auth import QueuePasswordProvider, QueueSmsProvider
from .contact import Roster
from .util import (
    dialog_chat_id,
    display_name,
    int_or_none,
    session_dirname,
    is_call_attachment,
    is_text_attachment_only,
    looks_like_incoming_call,
    opcode_name,
    parse_call_info,
    payload_as_dict,
    sender_id,
)

log = logging.getLogger("slidgemax.session")


def _extra_config() -> Any:
    from pymax import ExtraConfig

    # Slidge sessions are long-lived; let PyMax reconnect.
    kwargs: dict[str, Any] = {
        "reconnect": True,
        "reconnect_delay": 3.0,
    }
    try:
        return ExtraConfig(**kwargs)
    except TypeError:
        kwargs.pop("device_type", None)
        return ExtraConfig(**kwargs)


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
            self._me_id = int_or_none(getattr(self.client, "me", None))
        return self._me_id

    async def login(self) -> str | None:
        """
        Restore / create PyMax Client from stored legacy_module_data (phone) and session files.
        """
        phone = None
        data = getattr(self.user, "legacy_module_data", None) or {}
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
        try:
            return Client(**kwargs)
        except TypeError:
            # older pymax
            return Client(phone=phone, work_dir=str(self.work_dir), session_name="session.db")

    def _attach_handlers(self, client: Any) -> None:
        @client.on_start()
        async def _on_start(c: Any) -> None:
            self._me_id = int_or_none(getattr(c, "me", None))
            self._ready.set()
            log.info("MAX connected for %s (id=%s)", self.user.jid.bare, self._me_id)

        @client.on_message()
        async def _on_msg(message: Any, c: Any) -> None:
            await self._handle_incoming(message)

        try:

            @client.on_message_edit()
            async def _on_edit(message: Any, c: Any) -> None:
                await self._handle_incoming_edit(message)
        except Exception:
            log.debug("on_message_edit not available")

        @client.on_raw()
        async def _on_raw(frame: Any, c: Any) -> None:
            if not getattr(self.xmpp, "call_notifications", True):
                return
            op = getattr(frame, "opcode", None)
            pl = payload_as_dict(getattr(frame, "payload", None))
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
        finally:
            if not self._ready.is_set():
                # mark somehow
                pass

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

        body = getattr(message, "text", None)
        if not body:
            if getattr(self.xmpp, "placeholder_unsupported", True) and not is_text_attachment_only(message):
                body = getattr(self.xmpp, "unsupported_text", "Unsupported MAX content (not bridged).")
            else:
                return

        # call detection via attachment
        for att in getattr(message, "attaches", None) or []:
            if is_call_attachment(att):
                await self._notify_call({}, message)
                return

        max_id = int_or_none(getattr(message, "id", None))
        # For corrections from legacy, Slidge supports contact.correct(legacy_msg_id, text)
        # We send as normal; if this message is itself a correction we will handle in _handle_incoming_edit
        contact.send_text(str(body), legacy_msg_id=str(max_id) if max_id else None)

    async def _handle_incoming_edit(self, message: Any) -> None:
        me = self.me_id
        sid = sender_id(message)
        if me is not None and sid == me:
            return
        peer = self._resolve_peer(message)
        if peer is None:
            return
        contact = await self.contacts.by_legacy_id(str(peer))
        body = getattr(message, "text", None) or ""
        max_id = int_or_none(getattr(message, "id", None))
        if max_id is not None:
            contact.correct(str(max_id), str(body))

    def _resolve_peer(self, message: Any) -> int | None:
        me = self.me_id
        chat_id = int_or_none(getattr(message, "chat_id", None))
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
        if not getattr(self.xmpp, "call_notifications", True):
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

    # --- outgoing from XMPP ---

    async def on_text(self, chat: Any, text: str) -> str | None:  # not always called; prefer Contact
        # Fallback path
        return None

    # Called via Contact.on_message
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
        return [x for x in (getattr(c, "contacts", None) or []) if x]

    def dialogs_list(self) -> list[Any]:
        c = self.client
        if c is None:
            return []
        return list(getattr(c, "dialogs", None) or [])

    def chats_list(self) -> list[Any]:
        c = self.client
        if c is None:
            return []
        return list(getattr(c, "chats", None) or [])

    async def lookup_user(self, user_id: int) -> Any | None:
        c = self.client
        if c is None:
            return None
        getter = getattr(c, "get_user", None)
        if not callable(getter):
            return None
        try:
            return await getter(user_id)
        except Exception:
            return None

    async def search_by_phone(self, phone: str) -> Any | None:
        c = self.client
        if c is None:
            return None
        fn = getattr(c, "search_by_phone", None)
        if not callable(fn):
            return None
        try:
            return await fn(phone)
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
            for name in ("stop", "close", "disconnect"):
                m = getattr(client, name, None)
                if callable(m):
                    try:
                        res = m()
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        pass
                    break
        if task:
            try:
                await task
            except Exception:
                pass
