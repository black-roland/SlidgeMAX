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
from typing import TYPE_CHECKING, Any

from max_transport.auth import QueuePasswordProvider, QueueSmsProvider
from max_transport.jids import bare_jid
from max_transport.session import MaxSession
from max_transport.store import RegisteredUser
from max_transport.util import normalize_phone

if TYPE_CHECKING:
    from max_transport.component import MaxComponent

log = logging.getLogger("max_transport.adhoc")


def _form(xmpp: Any, title: str, instructions: str) -> Any:
    form = xmpp["xep_0004"].make_form("form", title)
    form["instructions"] = instructions
    return form


def register_commands(component: MaxComponent) -> None:
    adhoc = component["xep_0050"]
    adhoc.add_command(
        node="register",
        name="Register MAX account",
        handler=lambda iq, session: _handle_register(component, iq, session),
    )
    adhoc.add_command(
        node="unregister",
        name="Unregister MAX account",
        handler=lambda iq, session: _handle_unregister(component, iq, session),
    )
    adhoc.add_command(
        node="status",
        name="MAX transport status",
        handler=lambda iq, session: _handle_status(component, iq, session),
    )
    adhoc.add_command(
        node="reconnect",
        name="Reconnect MAX session",
        handler=lambda iq, session: _handle_reconnect(component, iq, session),
    )


def _async_next(coro_fn: Any, *bound: Any) -> Any:
    async def wrapper(*args: Any) -> Any:
        return await coro_fn(*bound, *args)
    return wrapper


def _deny(session: dict[str, Any], text: str) -> dict[str, Any]:
    session["notes"] = [("error", text)]
    session["payload"] = None
    session["next"] = None
    session["has_next"] = False
    return session


def _handle_register(component: MaxComponent, iq: Any, session: dict[str, Any]) -> dict[str, Any]:
    jid = bare_jid(str(session.get("from") or iq["from"]))
    if not component.config.registration_allowed(jid):
        return _deny(session, "Your JID is not allowed to register on this transport.")
    if component.users.get(jid) is not None:
        return _deny(session, "Already registered. Unregister first if you want to switch accounts.")

    form = _form(
        component,
        "MAX phone number",
        "Enter the phone number of your MAX account. An SMS code will be requested next.",
    )
    form.add_field(var="phone", ftype="text-single", label="Phone number", desc="+79990000000")
    session["payload"] = form
    session["next"] = _async_next(_register_phone, component)
    session["has_next"] = True
    session["allow_complete"] = False
    return session


async def _register_phone(component: MaxComponent, payload: Any, session: dict[str, Any]) -> dict[str, Any]:
    jid = bare_jid(str(session["from"]))
    values = payload.get_values() if hasattr(payload, "get_values") else payload["values"]
    raw_phone = str(values.get("phone") or "").strip()
    try:
        phone = normalize_phone(raw_phone)
    except ValueError as exc:
        return _deny(session, str(exc))

    user = RegisteredUser(jid=jid, phone=phone)
    max_session = MaxSession(
        component.config,
        user,
        sms_provider=QueueSmsProvider(),
        password_provider=QueuePasswordProvider(),
    )
    max_session.set_handlers(
        on_message=component.on_max_message,
        on_edit=component.on_max_edit,
        on_call=component.on_max_call,
        on_ready=component.on_max_ready,
    )
    session["max_session"] = max_session
    await max_session.start()

    form = _form(
        component,
        "SMS code",
        f"MAX is sending an SMS to {phone}. Enter the code.",
    )
    form.add_field(var="code", ftype="text-single", label="SMS code")
    session["payload"] = form
    session["next"] = _async_next(_register_sms, component)
    session["has_next"] = True
    session["cancel"] = _async_next(_cancel_register, component)
    return session


async def _register_sms(component: MaxComponent, payload: Any, session: dict[str, Any]) -> dict[str, Any]:
    max_session: MaxSession = session["max_session"]
    values = payload.get_values() if hasattr(payload, "get_values") else payload["values"]
    code = str(values.get("code") or "").strip()
    if not code:
        return _deny(session, "SMS code is required.")
    assert max_session.sms_provider is not None
    await max_session.sms_provider.set_code(code)
    try:
        await max_session.wait_ready(component.config.bridge.registration_timeout_seconds)
    except TimeoutError:
        await _cancel_register(component, session)
        return _deny(session, "MAX login timed out waiting after the SMS code.")
    except Exception as exc:
        await _cancel_register(component, session)
        return _deny(session, f"MAX login failed: {exc}")

    if max_session.password_provider is not None and max_session.password_provider.requested.is_set():
        hint = max_session.password_provider.hint or "account password"
        form = _form(component, "Two-factor password", f"MAX asked for 2FA. Hint: {hint}")
        form.add_field(var="password", ftype="text-private", label="2FA password")
        session["payload"] = form
        session["next"] = _async_next(_register_2fa, component)
        session["has_next"] = True
        return session

    return await _finish_register(component, session)


async def _register_2fa(component: MaxComponent, payload: Any, session: dict[str, Any]) -> dict[str, Any]:
    max_session: MaxSession = session["max_session"]
    values = payload.get_values() if hasattr(payload, "get_values") else payload["values"]
    password = str(values.get("password") or "")
    if not password:
        return _deny(session, "2FA password is required.")
    assert max_session.password_provider is not None
    max_session.password_provider.requested.clear()
    await max_session.password_provider.set_password(password)
    try:
        await max_session.wait_ready(component.config.bridge.registration_timeout_seconds)
    except Exception as exc:
        await _cancel_register(component, session)
        return _deny(session, f"MAX login failed: {exc}")
    if max_session.password_provider.requested.is_set() and not max_session._ready.is_set():
        await _cancel_register(component, session)
        return _deny(session, "MAX rejected the 2FA password.")
    return await _finish_register(component, session)


async def _finish_register(component: MaxComponent, session: dict[str, Any]) -> dict[str, Any]:
    max_session: MaxSession = session["max_session"]
    component.users.save(max_session.user)
    component.sessions.attach(max_session)
    await component.on_max_ready(max_session)
    name = max_session.user.max_name or "MAX"
    ident = max_session.user.max_user_id or "?"
    session["notes"] = [
        ("info", f"Linked {name} (id {ident}). Contacts will appear as subscriptions from this gateway.")
    ]
    session["payload"] = None
    session["next"] = None
    session["has_next"] = False
    log.info("registered %s as MAX %s", max_session.jid, ident)
    return session


async def _cancel_register(component: MaxComponent, session: dict[str, Any]) -> dict[str, Any]:
    max_session: MaxSession | None = session.get("max_session")
    if max_session is not None:
        await max_session.stop()
        max_session.wipe_session_files()
    session["notes"] = [("info", "Registration cancelled.")]
    session["payload"] = None
    session["next"] = None
    return session


def _handle_unregister(component: MaxComponent, iq: Any, session: dict[str, Any]) -> dict[str, Any]:
    jid = bare_jid(str(session.get("from") or iq["from"]))
    user = component.users.get(jid)
    if user is None:
        return _deny(session, "You are not registered.")
    form = _form(component, "Unregister", f"Unlink MAX account {user.phone} from {jid}?")
    form.add_field(var="confirm", ftype="boolean", label="Yes, unregister", value=False)
    session["payload"] = form
    session["next"] = _async_next(_unregister_confirm, component)
    session["has_next"] = False
    session["allow_complete"] = True
    return session


async def _unregister_confirm(component: MaxComponent, payload: Any, session: dict[str, Any]) -> dict[str, Any]:
    jid = bare_jid(str(session["from"]))
    values = payload.get_values() if hasattr(payload, "get_values") else payload["values"]
    confirm = str(values.get("confirm") or "").lower() in {"1", "true", "yes"}
    if not confirm:
        session["notes"] = [("info", "Still registered.")]
        session["payload"] = None
        session["next"] = None
        return session
    await component.sessions.unregister(jid)
    session["notes"] = [("info", "MAX account unlinked. Session files removed.")]
    session["payload"] = None
    session["next"] = None
    return session


def _handle_status(component: MaxComponent, iq: Any, session: dict[str, Any]) -> dict[str, Any]:
    jid = bare_jid(str(session.get("from") or iq["from"]))
    user = component.users.get(jid)
    max_session = component.sessions.get(jid)
    if user is None:
        notes = "Not registered. Run the Register command."
    else:
        ready = max_session is not None and max_session._ready.is_set()
        notes = (
            f"Phone: {user.phone}\n"
            f"MAX id: {user.max_user_id or 'unknown'}\n"
            f"Name: {user.max_name or 'unknown'}\n"
            f"Session: {'connected' if ready else 'offline'}"
        )
    session["notes"] = [("info", notes)]
    session["payload"] = None
    session["next"] = None
    return session


def _handle_reconnect(component: MaxComponent, iq: Any, session: dict[str, Any]) -> dict[str, Any]:
    jid = bare_jid(str(session.get("from") or iq["from"]))
    user = component.users.get(jid)
    if user is None:
        return _deny(session, "You are not registered.")
    session["payload"] = None
    session["next"] = None

    async def _go() -> None:
        await component.sessions.stop_user(jid)
        new_session = await component.sessions.start_user(user)
        new_session.set_handlers(
            on_message=component.on_max_message,
            on_edit=component.on_max_edit,
            on_call=component.on_max_call,
            on_ready=component.on_max_ready,
        )

    asyncio.create_task(_go())
    session["notes"] = [("info", "Reconnecting MAX session.")]
    return session
