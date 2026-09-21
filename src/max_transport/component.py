from __future__ import annotations

import logging
import uuid
from typing import Any

from slixmpp.componentxmpp import ComponentXMPP
from slixmpp.jid import JID
from slixmpp.xmlstream import ET

from max_transport.adhoc import register_commands
from max_transport.config import Config
from max_transport.jids import bare_jid, contact_jid, parse_contact_jid
from max_transport.session import MaxSession, SessionManager
from max_transport.store import MessageIdStore, RegisteredUser, UserStore
from max_transport.util import (
    display_name,
    int_or_none,
    is_text_attachment_only,
    normalize_phone,
    parse_call_info,
    sender_id,
)

log = logging.getLogger("max_transport.component")

NS_GATEWAY = "jabber:iq:gateway"
NS_ORIGIN = "urn:xmpp:sid:0"
NS_REPLACE = "urn:xmpp:message-correct:0"
NS_NICK = "http://jabber.org/protocol/nick"


class MaxComponent(ComponentXMPP):
    def __init__(self, config: Config) -> None:
        super().__init__(
            config.component.jid,
            config.component.secret,
            config.component.server,
            config.component.port,
            use_jc_ns=config.component.use_jabber_client_ns,
        )
        self.config = config
        config.ensure_dirs()
        self.users = UserStore(config.users_dir)
        self.msgids = MessageIdStore(config.msgid_path)
        self.sessions = SessionManager(config, self.users)

        self.register_plugin("xep_0030")
        self.register_plugin("xep_0004")
        self.register_plugin("xep_0050")
        self.register_plugin("xep_0077")
        self.register_plugin(
            "xep_0100",
            {
                "component_name": config.component.name,
                "type": "max",
                "needs_registration": True,
            },
        )
        self.register_plugin("xep_0199", {"keepalive": True, "frequency": 60})
        for plugin in ("xep_0308", "xep_0359"):
            try:
                self.register_plugin(plugin)
            except Exception:
                log.debug("plugin %s not available", plugin)

        self.add_event_handler("session_start", self.on_session_start)
        self.add_event_handler("legacy_login", self.on_legacy_login)
        self.add_event_handler("legacy_logout", self.on_legacy_logout)
        self.add_event_handler("legacy_message", self.on_legacy_message)
        self.add_event_handler("gateway_message", self.on_gateway_message)
        self.add_event_handler("presence_subscribe", self.on_presence_subscribe)
        self.add_event_handler("presence_unsubscribe", self.on_presence_unsubscribe)
        self.add_event_handler("disconnected", self.on_disconnected)

        self["xep_0077"].api.register(self._ibr_user_get, "user_get")
        self["xep_0077"].api.register(self._ibr_user_remove, "user_remove")
        self["xep_0077"].api.register(self._ibr_user_validate, "user_validate")
        self["xep_0077"].api.register(self._ibr_make_form, "make_registration_form")

        self["xep_0100"].api.register(self._legacy_contact_add, "legacy_contact_add")
        self["xep_0100"].api.register(self._legacy_contact_remove, "legacy_contact_remove")

        self._register_gateway_stanza()

    def _register_gateway_stanza(self) -> None:
        from slixmpp.stanza import Iq
        from slixmpp.xmlstream import register_stanza_plugin
        from slixmpp.xmlstream.handler import Callback
        from slixmpp.xmlstream.matcher import StanzaPath
        from slixmpp.xmlstream.stanzabase import ElementBase

        class GatewayQuery(ElementBase):
            name = "query"
            namespace = NS_GATEWAY
            plugin_attrib = "gateway_query"
            interfaces = {"desc", "prompt", "jid"}
            sub_interfaces = interfaces

        register_stanza_plugin(Iq, GatewayQuery)
        self.register_handler(
            Callback(
                "MaxGatewayQuery",
                StanzaPath("iq/gateway_query"),
                self._on_gateway_query,
            )
        )

    async def on_session_start(self, _event: Any) -> None:
        log.info("connected as component %s", self.boundjid.host)
        self["xep_0030"].add_identity(
            category="gateway",
            itype="max",
            name=self.config.component.name,
        )
        self["xep_0030"].add_feature(NS_GATEWAY)
        register_commands(self)
        if self.config.bridge.always_online:
            for user in self.users.list():
                session = await self.sessions.start_user(user)
                session.set_handlers(
                    on_message=self.on_max_message,
                    on_edit=self.on_max_edit,
                    on_call=self.on_max_call,
                    on_ready=self.on_max_ready,
                )

    async def on_disconnected(self, _event: Any) -> None:
        log.warning("component disconnected from %s", self.config.component.server)

    async def shutdown(self) -> None:
        await self.sessions.stop_all()
        self.msgids.close()
        result = self.disconnect()
        if hasattr(result, "__await__"):
            await result

    async def on_legacy_login(self, event: Any) -> None:
        jid = bare_jid(str(_event_jid(event)))
        user = self.users.get(jid)
        if user is None:
            return
        session = self.sessions.get(jid)
        if session is None:
            session = await self.sessions.start_user(user)
            session.set_handlers(
                on_message=self.on_max_message,
                on_edit=self.on_max_edit,
                on_call=self.on_max_call,
                on_ready=self.on_max_ready,
            )
        if session._ready.is_set():
            await self.push_contacts(session)

    async def on_legacy_logout(self, event: Any) -> None:
        jid = bare_jid(str(_event_jid(event)))
        if self.config.bridge.always_online:
            session = self.sessions.get(jid)
            if session is not None:
                self._send_contacts_unavailable(session)
            return
        await self.sessions.stop_user(jid)

    async def on_legacy_message(self, msg: Any) -> None:
        if msg["type"] not in {"chat", "normal", ""}:
            return
        from_jid = bare_jid(str(msg["from"]))
        to_jid = str(msg["to"])
        session = self.sessions.get(from_jid)
        if session is None:
            self._bounce(msg, "Not registered on this MAX transport. Use the Register ad-hoc command.")
            return
        if not session._ready.is_set():
            self._bounce(msg, "MAX session is not connected yet.")
            return
        peer_id = parse_contact_jid(to_jid, self.config.component.jid)
        if peer_id is None:
            return
        body = str(msg["body"] or "")
        replace_id = _replace_id(msg)
        if replace_id:
            mapped = self.msgids.max_ids_for(from_jid, replace_id)
            if mapped is None:
                self._bounce(msg, "Cannot edit: original message is not known to the transport.")
                return
            chat_id, max_msg_id = mapped
            try:
                await session.edit_text(chat_id, max_msg_id, body)
            except Exception as exc:
                self._bounce(msg, f"MAX edit failed: {exc}")
            return
        if not body.strip():
            return
        try:
            sent = await session.send_text(peer_id, body)
        except Exception as exc:
            self._bounce(msg, f"MAX send failed: {exc}")
            return
        xmpp_id = _origin_id(msg) or str(msg["id"] or uuid.uuid4())
        max_msg_id = int_or_none(getattr(sent, "id", None))
        chat_id = int_or_none(getattr(sent, "chat_id", None))
        if max_msg_id is not None:
            if chat_id is None and session.me_id is not None:
                chat_id = session.me_id ^ peer_id
            if chat_id is not None:
                self.msgids.put(from_jid, chat_id, max_msg_id, xmpp_id)

    async def on_gateway_message(self, msg: Any) -> None:
        if msg["type"] not in {"chat", "normal", ""}:
            return
        body = str(msg["body"] or "").strip()
        from_jid = bare_jid(str(msg["from"]))
        reply = self._chat_help(from_jid) if body.lower() in {"", "help", "?"} else self._chat_command(from_jid, body)
        self.send_message(
            mto=from_jid,
            mfrom=self.boundjid.bare,
            mbody=reply,
            mtype="chat",
        )

    def _chat_help(self, jid: str) -> str:
        user = self.users.get(jid)
        if user is None:
            return (
                "MAX transport. Registration is via ad-hoc commands "
                "(XEP-0050) on this component.\n"
                "In Gajim: service discovery → this gateway → Execute command → Register MAX account.\n"
                "Commands here: help, status."
            )
        return (
            f"Registered as {user.phone} (MAX id {user.max_user_id or '?'}).\n"
            "Ad-hoc commands: status, unregister, reconnect.\n"
            "Chat with contacts at <max-id>@" + self.config.component.jid
        )

    def _chat_command(self, jid: str, body: str) -> str:
        cmd = body.split(None, 1)[0].lower()
        if cmd == "status":
            user = self.users.get(jid)
            session = self.sessions.get(jid)
            if user is None:
                return "Not registered."
            ready = session is not None and session._ready.is_set()
            return f"{user.phone} / MAX {user.max_user_id} / {'connected' if ready else 'offline'}"
        return "Unknown command. Send help."

    async def on_presence_subscribe(self, presence: Any) -> None:
        from_jid = bare_jid(str(presence["from"]))
        to_jid = str(presence["to"])
        if bare_jid(to_jid) == self.boundjid.bare:
            self.send_presence(pto=from_jid, pfrom=self.boundjid.bare, ptype="subscribed")
            self.send_presence(pto=from_jid, pfrom=self.boundjid.bare)
            return
        session = self.sessions.get(from_jid)
        peer_id = parse_contact_jid(to_jid, self.config.component.jid)
        if session is None or peer_id is None:
            return
        if self.config.bridge.auto_subscribe:
            await session.add_contact(peer_id)
            contact = contact_jid(peer_id, self.config.component.jid)
            self.send_presence(pto=from_jid, pfrom=contact, ptype="subscribed")
            user = await session.lookup_user(peer_id)
            self._send_contact_presence(from_jid, peer_id, display_name(user, fallback=f"MAX {peer_id}"))

    async def on_presence_unsubscribe(self, presence: Any) -> None:
        from_jid = bare_jid(str(presence["from"]))
        peer_id = parse_contact_jid(str(presence["to"]), self.config.component.jid)
        session = self.sessions.get(from_jid)
        if session is None or peer_id is None:
            return
        await session.remove_contact(peer_id)
        contact = contact_jid(peer_id, self.config.component.jid)
        self.send_presence(pto=from_jid, pfrom=contact, ptype="unsubscribed")
        self.send_presence(pto=from_jid, pfrom=contact, ptype="unavailable")

    async def on_max_ready(self, session: MaxSession) -> None:
        self.users.save(session.user)
        if self.config.bridge.sync_contacts_on_login:
            await self.push_contacts(session)

    async def push_contacts(self, session: MaxSession) -> None:
        seen: set[int] = set()
        me_id = session.me_id
        for user in session.contacts():
            ident = int_or_none(user)
            if ident is None or ident == me_id:
                continue
            seen.add(ident)
            self._send_contact_presence(session.jid, ident, display_name(user))
        for chat in session.dialogs() + session.chats():
            ident = int_or_none(getattr(chat, "owner", None))
            if ident is None or ident == me_id or ident in seen:
                continue
            t = str(getattr(getattr(chat, "type", None), "name", getattr(chat, "type", ""))).upper()
            if t and "DIALOG" not in t and ("CHAT" in t or "CHANNEL" in t or "GROUP" in t):
                continue
            if me_id is not None:
                chat_id = int_or_none(getattr(chat, "id", None))
                if chat_id is not None:
                    ident = ident or (chat_id ^ me_id)
            if ident is None or ident in seen:
                continue
            seen.add(ident)
            name = display_name(None, fallback=getattr(chat, "title", None) or f"MAX {ident}")
            self._send_contact_presence(session.jid, ident, name)

    def _send_contacts_unavailable(self, session: MaxSession) -> None:
        me_id = session.me_id
        for user in session.contacts():
            ident = int_or_none(user)
            if ident is None or ident == me_id:
                continue
            contact = contact_jid(ident, self.config.component.jid)
            self.send_presence(pto=session.jid, pfrom=contact, ptype="unavailable")

    def _send_contact_presence(self, user_jid: str, max_id: int, name: str) -> None:
        contact = contact_jid(max_id, self.config.component.jid)
        self.send_presence(pto=user_jid, pfrom=contact, ptype="subscribe")
        pres = self.make_presence(pto=user_jid, pfrom=contact)
        nick = ET.Element(f"{{{NS_NICK}}}nick")
        nick.text = name
        pres.append(nick)
        pres.send()

    async def on_max_message(self, session: MaxSession, message: Any) -> None:
        me_id = session.me_id
        sid = sender_id(message)
        if me_id is not None and sid == me_id:
            return
        peer_id = session.resolve_dialog_peer(message)
        if peer_id is None:
            return
        body = getattr(message, "text", None)
        if not body:
            if self.config.bridge.placeholder_unsupported and not is_text_attachment_only(message):
                body = self.config.bridge.unsupported_text
            else:
                return
        xmpp_id = f"max-{int_or_none(getattr(message, 'id', None)) or uuid.uuid4()}"
        max_msg_id = int_or_none(getattr(message, "id", None))
        chat_id = int_or_none(getattr(message, "chat_id", None))
        if max_msg_id is not None and chat_id is not None:
            self.msgids.put(session.jid, chat_id, max_msg_id, xmpp_id)
        self._send_chat(session.jid, peer_id, str(body), xmpp_id=xmpp_id)

    async def on_max_edit(self, session: MaxSession, message: Any) -> None:
        me_id = session.me_id
        sid = sender_id(message)
        if me_id is not None and sid == me_id:
            return
        peer_id = session.resolve_dialog_peer(message)
        if peer_id is None:
            return
        body = getattr(message, "text", None) or ""
        max_msg_id = int_or_none(getattr(message, "id", None))
        replace = self.msgids.xmpp_id_for(session.jid, max_msg_id) if max_msg_id is not None else None
        new_id = f"max-{max_msg_id}-edit-{uuid.uuid4().hex[:8]}"
        self._send_chat(session.jid, peer_id, str(body), xmpp_id=new_id, replace_id=replace)

    async def on_max_call(self, session: MaxSession, message: Any, extra: Any) -> None:
        if not self.config.bridge.call_notifications:
            return
        info: dict[str, Any]
        if isinstance(extra, dict):
            info = extra
        else:
            info = parse_call_info({})
            if message is not None:
                info["caller_id"] = session.resolve_dialog_peer(message) or sender_id(message)
        caller_id = int_or_none(info.get("caller_id"))
        if caller_id is None and message is not None:
            caller_id = session.resolve_dialog_peer(message)
        if caller_id is None:
            return
        text = self.config.bridge.call_video_text if info.get("video") else self.config.bridge.call_voice_text
        name = info.get("name")
        if name:
            text = f"{text} ({name})"
        self._send_chat(session.jid, caller_id, text, xmpp_id=f"max-call-{uuid.uuid4().hex[:12]}")

    def _send_chat(
        self,
        user_jid: str,
        peer_id: int,
        body: str,
        *,
        xmpp_id: str,
        replace_id: str | None = None,
    ) -> None:
        contact = contact_jid(peer_id, self.config.component.jid)
        msg = self.make_message(mto=user_jid, mfrom=contact, mbody=body, mtype="chat")
        origin = ET.Element(f"{{{NS_ORIGIN}}}origin-id")
        origin.set("id", xmpp_id)
        msg.append(origin)
        msg["id"] = xmpp_id
        if replace_id:
            replace = ET.Element(f"{{{NS_REPLACE}}}replace")
            replace.set("id", replace_id)
            msg.append(replace)
        msg.send()

    def _bounce(self, original: Any, text: str) -> None:
        self.send_message(
            mto=original["from"],
            mfrom=original["to"],
            mbody=text,
            mtype="error",
        )

    def _on_gateway_query(self, iq: Any) -> None:
        self.loop.create_task(self._handle_gateway_query(iq))

    async def _handle_gateway_query(self, iq: Any) -> None:
        ifrom = bare_jid(str(iq["from"]))
        session = self.sessions.get(ifrom)
        if iq["type"] == "get":
            reply = iq.reply()
            reply["gateway_query"]["desc"] = "MAX user ID or phone number"
            reply["gateway_query"]["prompt"] = "Contact"
            reply.send()
            return
        if iq["type"] != "set":
            return
        prompt = str(iq["gateway_query"]["prompt"] or "").strip()
        if not prompt:
            iq.reply().error()
            return
        user_id = None
        if prompt.isdigit():
            user_id = int(prompt)
        elif session is not None:
            try:
                phone = normalize_phone(prompt)
            except ValueError:
                phone = None
            if phone:
                found = await session.search_phone(phone)
                user_id = int_or_none(found)
        if user_id is None:
            err = iq.reply()
            err["type"] = "error"
            err.send()
            return
        reply = iq.reply()
        reply["gateway_query"]["jid"] = contact_jid(user_id, self.config.component.jid)
        reply.send()

    async def _legacy_contact_add(self, _jid: Any, _node: Any, ifrom: JID, args: JID) -> None:
        session = self.sessions.get(bare_jid(str(ifrom)))
        peer_id = parse_contact_jid(str(args), self.config.component.jid)
        if session is None or peer_id is None:
            return
        await session.add_contact(peer_id)

    async def _legacy_contact_remove(self, _jid: Any, _node: Any, ifrom: JID, args: JID) -> None:
        session = self.sessions.get(bare_jid(str(ifrom)))
        peer_id = parse_contact_jid(str(args), self.config.component.jid)
        if session is None or peer_id is None:
            return
        await session.remove_contact(peer_id)

    def _ibr_user_get(self, _jid: Any, _node: Any, ifrom: JID, _iq: Any) -> dict[str, str] | None:
        user = self.users.get(bare_jid(str(ifrom)))
        if user is None:
            return None
        return {"username": user.phone, "password": ""}

    def _ibr_user_remove(self, _jid: Any, _node: Any, ifrom: JID, _iq: Any) -> None:
        jid = bare_jid(str(ifrom))
        if self.users.get(jid) is None:
            raise KeyError(jid)
        self.loop.create_task(self.sessions.unregister(jid))

    def _ibr_user_validate(self, _jid: Any, _node: Any, ifrom: JID, _registration: Any) -> None:
        raise ValueError(
            "Use the Register ad-hoc command on this component (XEP-0050). "
            "In-band registration cannot collect an SMS code."
        )

    def _ibr_make_form(self, _jid: Any, _node: Any, ifrom: JID, iq: Any) -> Any:
        reply = iq.reply()
        user = self.users.get(bare_jid(str(ifrom)))
        form = self["xep_0004"].make_form("form", "MAX transport")
        if user is not None:
            reply["register"]["registered"] = True
            form["instructions"] = (
                f"Already registered as {user.phone}. "
                "Unregister via the Unregister ad-hoc command."
            )
        else:
            form["instructions"] = (
                "Registration is an ad-hoc command (Register MAX account) on this gateway. "
                "That flow asks for your phone number, SMS code, and 2FA if needed."
            )
        reply["register"].append(form)
        return reply


def _event_jid(event: Any) -> str:
    if hasattr(event, "jid"):
        return str(event.jid)
    if isinstance(event, dict) and "jid" in event:
        return str(event["jid"])
    if isinstance(event, dict) and "from" in event:
        return str(event["from"])
    return str(event)


def _origin_id(msg: Any) -> str | None:
    try:
        value = msg["origin_id"]["id"]
        if value:
            return str(value)
    except Exception:
        pass
    orig = msg.xml.find(f"{{{NS_ORIGIN}}}origin-id")
    if orig is not None:
        return orig.get("id")
    return None


def _replace_id(msg: Any) -> str | None:
    try:
        value = msg["replace"]["id"]
        if value:
            return str(value)
    except Exception:
        pass
    el = msg.xml.find(f"{{{NS_REPLACE}}}replace")
    if el is not None:
        return el.get("id")
    return None
