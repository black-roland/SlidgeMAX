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

"""XMPP component: registration, identity, and pending MAX logins."""

from __future__ import annotations

import logging
import shutil
from typing import TYPE_CHECKING

from slidge import BaseGateway
from slidge.command import FormField
from slidge.command.register import RegistrationType, TwoFactorNotRequired
from slidge.db import GatewayUser
from slidge.db.meta import JSONSerializable
from slixmpp import JID
from slixmpp.exceptions import XMPPError

from . import config
from .auth import PendingAuth
from .session import Session, make_client, session_dir
from .util import display_name, normalize_phone, user_id

if TYPE_CHECKING:
    from pymax.client import Client

log = logging.getLogger(__name__)

_PASSWORD_REQUIRED = (
    "This MAX account requires a two-factor password. Register again and fill "
    "the 2FA password field on the first form."
)


class Gateway(BaseGateway[Session]):
    COMPONENT_NAME = "MAX Gateway"
    COMPONENT_TYPE = "max"

    REGISTRATION_TYPE = RegistrationType.TWO_FACTOR_CODE
    REGISTRATION_INSTRUCTIONS = (
        "Enter the phone number of your MAX account. MAX will text you a code next. "
        "If the account has a two-factor password, fill it in now."
    )
    REGISTRATION_FIELDS = [
        FormField(var="phone", label="Phone number", required=True),
        FormField(
            var="password",
            label="2FA password (only if your MAX account has one)",
            required=False,
            private=True,
        ),
    ]
    REGISTRATION_2FA_TITLE = "SMS code"
    REGISTRATION_2FA_INSTRUCTIONS = "Enter the SMS code MAX sent to your phone."

    ROSTER_GROUP = "MAX"
    GROUPS = False
    THREADS = False

    SEARCH_TITLE = "Find a MAX contact"
    SEARCH_INSTRUCTIONS = "Enter a MAX user id or a phone number."
    SEARCH_FIELDS = [
        FormField(var="query", label="MAX user ID or phone number", required=True),
    ]

    WELCOME_MESSAGE = (
        "Linked to MAX. Contacts will show up as <id>@ this gateway. "
        "Type 'help' for commands, or start chatting."
    )

    def __init__(self) -> None:
        super().__init__()
        self.pending: dict[str, PendingAuth] = {}

    async def validate(
        self, user_jid: JID, registration_form: JSONSerializable
    ) -> JSONSerializable | None:
        try:
            phone = normalize_phone(str(registration_form.get("phone") or ""))
        except ValueError as exc:
            raise XMPPError("bad-request", str(exc)) from exc
        password = str(registration_form.get("password") or "")

        await self._abort_pending(user_jid.bare)

        work_dir = session_dir(user_jid.bare)
        created_session = not (work_dir / "session.db").exists()
        pending = PendingAuth(
            phone=phone,
            password=password,
            work_dir=str(work_dir),
            created_session=created_session,
        )
        client = make_client(
            phone,
            work_dir,
            sms_provider=pending.sms,
            password_provider=pending.password_provider,
        )
        pending.client = client
        self._attach_pending_handlers(pending, client)
        pending.task = self.loop.create_task(
            self._run_pending(pending, client),
            name=f"max-register:{user_jid.bare}",
        )
        self.pending[user_jid.bare] = pending

        try:
            state = await pending.wait(config.REGISTRATION_TIMEOUT)
        except TimeoutError as exc:
            await self._abort_pending(user_jid.bare)
            raise XMPPError("remote-server-timeout", "MAX login timed out.") from exc
        except Exception as exc:
            await self._abort_pending(user_jid.bare)
            raise XMPPError("not-authorized", str(exc)) from exc

        if state == "password":
            if not password:
                await self._abort_pending(user_jid.bare)
                raise XMPPError("not-acceptable", _PASSWORD_REQUIRED)
            await pending.password_provider.set_password(password)
            try:
                state = await pending.wait(
                    config.REGISTRATION_TIMEOUT, ignore=frozenset({"password"})
                )
            except Exception as exc:
                await self._abort_pending(user_jid.bare)
                raise XMPPError("not-authorized", str(exc)) from exc

        if state == "ready":
            registration_form.clear()
            registration_form.update(
                {
                    "phone": phone,
                    "max_user_id": pending.me_id,
                    "max_name": pending.me_name,
                }
            )
            raise TwoFactorNotRequired

        return {"phone": phone}

    async def validate_two_factor_code(
        self, user: GatewayUser, code: str
    ) -> JSONSerializable | None:
        pending = self.pending.get(user.jid.bare)
        if pending is None:
            raise XMPPError(
                "bad-request",
                "No pending MAX login. Start registration again.",
            )
        if not code.strip():
            raise XMPPError("bad-request", "SMS code is required.")

        await pending.sms.set_code(code)
        try:
            state = await pending.wait(
                config.REGISTRATION_TIMEOUT, ignore=frozenset({"sms"})
            )
        except TimeoutError as exc:
            await self._abort_pending(user.jid.bare)
            raise XMPPError(
                "remote-server-timeout", "MAX login timed out after the SMS code."
            ) from exc
        except Exception as exc:
            await self._abort_pending(user.jid.bare)
            raise XMPPError("not-authorized", str(exc)) from exc

        if state == "password":
            password = pending.password or str(user.get("password") or "")
            if not password:
                await self._abort_pending(user.jid.bare)
                raise XMPPError("not-acceptable", _PASSWORD_REQUIRED)
            pending.password_provider.requested.clear()
            await pending.password_provider.set_password(password)
            try:
                state = await pending.wait(
                    config.REGISTRATION_TIMEOUT,
                    ignore=frozenset({"sms"}),
                )
            except Exception as exc:
                await self._abort_pending(user.jid.bare)
                raise XMPPError("not-authorized", str(exc)) from exc
            if state == "password":
                await self._abort_pending(user.jid.bare)
                raise XMPPError("not-authorized", "MAX rejected the 2FA password.")

        if state != "ready":
            await self._abort_pending(user.jid.bare)
            raise XMPPError(
                "not-authorized",
                "MAX login did not complete after the SMS code.",
            )

        return {
            "phone": pending.phone,
            "max_user_id": pending.me_id,
            "max_name": pending.me_name,
        }

    def _attach_pending_handlers(self, pending: PendingAuth, client: Client) -> None:
        @client.on_start()
        async def on_start(c: Client) -> None:
            pending.me_id = user_id(c.me)
            pending.me_name = display_name(c.me)
            pending.ready.set()
            log.info("Pending MAX login ready for %s (id=%s)", pending.phone, pending.me_id)

        @client.on_error()
        async def on_error(*args: object) -> None:
            exc = next((a for a in args if isinstance(a, BaseException)), None)
            pending.error = str(exc) if exc else "MAX client error"
            log.warning("Pending MAX login error for %s: %s", pending.phone, pending.error)
            if not pending.ready.is_set():
                pending.failed.set()

    async def _run_pending(self, pending: PendingAuth, client: Client) -> None:
        try:
            await client.start()
        except Exception as exc:
            pending.error = str(exc) or type(exc).__name__
            log.exception("Pending MAX login crashed for %s", pending.phone)
            pending.failed.set()

    async def _abort_pending(self, bare_jid: str) -> None:
        pending = self.pending.pop(bare_jid, None)
        if pending is None:
            return
        await self._shutdown_pending(pending)

    async def _shutdown_pending(self, pending: PendingAuth) -> None:
        if pending.task is not None:
            pending.task.cancel()
        if pending.client is not None:
            try:
                await pending.client.stop()
            except Exception:
                log.debug("pending client.stop() failed", exc_info=True)
        if pending.created_session and pending.work_dir:
            shutil.rmtree(pending.work_dir, ignore_errors=True)
