from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncIterator

from slidge import LegacyContact, LegacyRoster
from slidge.util.types import ContactMessage  # type: ignore

from .util import display_name, int_or_none

if TYPE_CHECKING:
    from .session import Session

log = logging.getLogger("slidgemax.contact")


class Contact(LegacyContact):
    """
    A MAX 1:1 contact.
    """

    session: "Session"

    async def update_info(self) -> None:
        uid = int_or_none(self.legacy_id)
        if uid is None:
            return
        user = await self.session.lookup_user(uid)
        self.name = display_name(user, fallback=f"MAX {uid}")

    async def on_message(self, message: ContactMessage) -> str | None:  # type: ignore[override]
        """
        XMPP user sent a message to this MAX contact.
        Return the MAX message id (as str) so Slidge can track it for corrections.
        """
        body = message.body or ""
        if not body.strip():
            return None

        replace = getattr(message, "replace", None)
        if replace:
            try:
                max_msg_id = int(replace)
                chat_id = self.session.me_id ^ int(self.legacy_id) if self.session.me_id else None
                if chat_id is not None:
                    await self.session.edit_text(chat_id, max_msg_id, body)
                    return str(max_msg_id)
            except Exception:
                log.debug("could not interpret replace id as MAX id", exc_info=True)

        sent = await self.session.send_text(int(self.legacy_id), body)
        mid = int_or_none(getattr(sent, "id", None))
        return str(mid) if mid is not None else None


class Roster(LegacyRoster[Contact]):
    """
    Maps JID localpart (numeric string) <-> MAX user id (int).
    """

    session: "Session"

    async def jid_username_to_legacy_id(self, jid_username: str) -> str:
        if not jid_username or not jid_username.isdigit():
            from slixmpp.exceptions import XMPPError

            raise XMPPError("item-not-found", "Only numeric MAX user IDs are supported")
        val = int(jid_username)
        if val <= 0:
            from slixmpp.exceptions import XMPPError

            raise XMPPError("item-not-found", "MAX user ID must be positive")
        return str(val)

    async def legacy_id_to_jid_username(self, legacy_id: str) -> str:
        return str(legacy_id)

    async def fill(self) -> AsyncIterator[Contact]:
        sess = self.session
        seen: set[int] = set()

        for u in sess.contacts_list():
            uid = int_or_none(u)
            if uid is None or uid in seen:
                continue
            seen.add(uid)
            c = await self.by_legacy_id(str(uid))
            await c.update_info()

        me = sess.me_id
        for d in sess.dialogs_list() + sess.chats_list():
            owner = int_or_none(getattr(d, "owner", None))
            if owner is None or (me is not None and owner == me):
                continue
            tname = str(getattr(getattr(d, "type", None), "name", getattr(d, "type", ""))).upper()
            if "DIALOG" not in tname and ("GROUP" in tname or "CHANNEL" in tname or tname == "CHAT"):
                continue
            cid = int_or_none(getattr(d, "id", None))
            if cid is not None and me is not None:
                peer = cid ^ me
                if peer in seen:
                    continue
                seen.add(peer)
                c = await self.by_legacy_id(str(peer))
                await c.update_info()

        # async generator protocol
        return
        yield  # type: ignore[misc]
