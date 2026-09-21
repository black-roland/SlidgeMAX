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

import logging
from typing import TYPE_CHECKING, Any, AsyncIterator

from slidge import LegacyContact, LegacyRoster
from slidge.util.types import ContactMessage  # type: ignore

from .util import dialog_peer_id, display_name, int_or_none

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
        if user is None:
            cached = getattr(self.session.client, "get_cached_user", None)
            if callable(cached):
                user = cached(uid)
        self.name = display_name(user, fallback=f"MAX {uid}")
        self.is_friend = True

    def _send(self, stanza: Any, carbon: bool = False, nick: bool = False, **send_kwargs: Any) -> Any:
        # Friends only get an XEP-0172 nick when nick=True. Gajim and Dino otherwise show the JID.
        return super()._send(stanza, carbon=carbon, nick=bool(self.name) or nick, **send_kwargs)

    async def backfill(self, after: Any) -> None:
        """History sync is out of scope. Must not raise, or roster fill stops."""
        return

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
        me = sess.me_id
        address_book = sess.contacts_list()
        chats = sess.chats_list()
        log.info(
            "filling roster me=%s address_book=%s chats=%s",
            me,
            len(address_book),
            len(chats),
        )
        if not chats and sess.client is not None:
            fetch = getattr(sess.client, "fetch_chats", None)
            if callable(fetch):
                try:
                    chats = list(await fetch() or [])
                except Exception:
                    log.exception("fetch_chats failed")
                else:
                    log.info("fetch_chats returned %s chats", len(chats))

        yielded = 0
        for user in address_book:
            contact = await self._friend(int_or_none(user), seen, me)
            if contact is not None:
                yielded += 1
                yield contact

        for chat in chats:
            contact = await self._friend(dialog_peer_id(chat, me), seen, me)
            if contact is not None:
                yielded += 1
                yield contact

        log.info("roster fill yielded %s contacts", yielded)

    async def _friend(
        self, uid: int | None, seen: set[int], me: int | None
    ) -> Contact | None:
        if uid is None or uid <= 0 or uid in seen or (me is not None and uid == me):
            return None
        seen.add(uid)
        contact = await self.by_legacy_id(str(uid))
        contact.is_friend = True
        if contact.name:
            log.info("roster name jid=%s name=%s", contact.jid.bare, contact.name)
            await contact.add_to_roster(force=True)
            if contact.added_to_roster:
                contact.xmpp.pubsub.broadcast_nick(
                    contact.session.user_jid,
                    contact.jid.bare,
                    contact.name,
                )
        return contact
