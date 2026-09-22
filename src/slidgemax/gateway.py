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
import functools
import logging
from pathlib import Path
from typing import Any

from slixmpp.exceptions import XMPPError

from slidge import BaseGateway, FormField
from slidge.command import BUILTIN_COMMANDS, Form
from slidge.command.register import Register, RegistrationType
from slidge.core import config as slidge_config
from slidge.db import GatewayUser

from .auth import QueuePasswordProvider, QueueSmsProvider
from .session import Session
from .util import int_or_none, normalize_phone, session_dirname

log = logging.getLogger("slidgemax.gateway")


class PasswordRequired(Exception):
    """MAX accepted the SMS code and is waiting for the account password."""

    def __init__(self, hint: str | None = None) -> None:
        self.hint = hint
        super().__init__("MAX password required")


class AuthAttempt:
    """Tracks an in-flight MAX registration (phone → SMS → optional password)."""

    def __init__(self, phone: str) -> None:
        self.phone = phone
        self.sms = QueueSmsProvider()
        self.password = QueuePasswordProvider()
        self.task: asyncio.Task[None] | None = None
        self.client: Any = None
        self.ready = asyncio.Event()
        self.failed = asyncio.Event()
        self.error: str | None = None
        self.max_user_id: int | None = None
        self.sms_done = False


class MaxRegister(Register):
    """Built-in register, plus a password form when MAX asks for account 2FA."""

    async def two_fa(
        self,
        form_values: dict[str, Any],
        _session: None,
        _ifrom: Any,
        user: GatewayUser,
    ) -> Form:
        assert isinstance(form_values["code"], str)
        try:
            data = await self.xmpp.validate_two_factor_code(user, form_values["code"])
        except PasswordRequired as exc:
            return self._password_form(user, exc.hint)
        if data is not None:
            user.legacy_module_data.update(data)
        return await self.preferences(user)

    def _password_form(
        self,
        user: GatewayUser,
        hint: str | None,
        *,
        retry: bool = False,
    ) -> Form:
        hint_text = f" Hint: {hint}." if hint else ""
        retry_text = " That password was rejected." if retry else ""
        return Form(
            title="MAX account password",
            instructions=(
                "MAX requires the account password (2FA)."
                f"{hint_text}{retry_text} "
                "Enter it in this form. Do not send it as a chat message."
            ),
            fields=[
                FormField(var="password", label="Password", required=True, private=True),
            ],
            handler=functools.partial(self._submit_password, user=user),
        )

    async def _submit_password(
        self,
        form_values: dict[str, Any],
        _session: None,
        _ifrom: Any,
        user: GatewayUser,
    ) -> Form:
        try:
            data = await self.xmpp.submit_max_password(
                user, str(form_values.get("password") or "")
            )
        except PasswordRequired as exc:
            return self._password_form(user, exc.hint, retry=True)
        if data is not None:
            user.legacy_module_data.update(data)
        return await self.preferences(user)


class Gateway(BaseGateway[Session]):
    """
    Slidge gateway for MAX messenger.
    """

    COMPONENT_NAME = "Slidge XMPP-MAX gateway"
    COMPONENT_TYPE = "max"
    ROSTER_GROUP = "MAX"
    GROUPS = False

    REGISTRATION_TYPE = RegistrationType.TWO_FACTOR_CODE
    REGISTRATION_FIELDS = [
        FormField(var="phone", label="Phone number", required=True),
    ]
    REGISTRATION_INSTRUCTIONS = (
        "Enter the phone number of your MAX account (e.g. +79991234567). "
        "You will then receive an SMS code."
    )
    REGISTRATION_2FA_TITLE = "SMS code"
    REGISTRATION_2FA_INSTRUCTIONS = (
        "MAX sent an SMS code to your phone. Enter it here. "
        "If the account has a 2FA password, the next form will ask for it."
    )
    COMMANDS = tuple(cls for cls in BUILTIN_COMMANDS if cls is not Register) + (MaxRegister,)

    WELCOME_MESSAGE = (
        "Thank you for registering with SlidgeMAX. "
        "Contacts will appear as subscriptions. Send messages to id@this-component."
    )

    call_notifications: bool = True
    placeholder_unsupported: bool = True
    unsupported_text: str = "Unsupported MAX content (not bridged)."

    def __init__(self) -> None:
        super().__init__()
        self._pending: dict[str, AuthAttempt] = {}
        self._sessions_dir: Path | None = None

    @property
    def sessions_dir(self) -> Path:
        if self._sessions_dir is None:
            base = slidge_config.HOME_DIR
            self._sessions_dir = Path(base) / "max-sessions"
            self._sessions_dir.mkdir(parents=True, exist_ok=True)
        return self._sessions_dir

    def _get_pending(self, jid: str) -> AuthAttempt | None:
        return self._pending.get(jid)

    def _cleanup_pending(self, jid: str) -> None:
        self._pending.pop(jid, None)

    async def validate(
        self, user_jid: Any, registration_form: dict[str, Any]
    ) -> dict[str, Any] | None:
        raw_phone = str(registration_form.get("phone") or "").strip()
        try:
            phone = normalize_phone(raw_phone)
        except ValueError as exc:
            raise XMPPError("bad-request", str(exc)) from exc

        bare = str(user_jid.bare)
        if bare in self._pending:
            old = self._pending.pop(bare)
            await self._release_client(old)

        attempt = AuthAttempt(phone)
        self._pending[bare] = attempt

        client = self._make_temp_client(bare, phone, attempt.sms, attempt.password)
        attempt.client = client
        log.info(
            "registration started jid=%s phone=%s work_dir=%s",
            bare,
            phone,
            client.work_dir,
        )

        async def _runner() -> None:
            try:
                await client.start()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                attempt.error = str(exc) or exc.__class__.__name__
                attempt.failed.set()
                log.exception("Registration client failed for %s: %s", bare, exc)
            finally:
                if not attempt.ready.is_set():
                    attempt.failed.set()

        attempt.task = asyncio.create_task(_runner(), name=f"slidgemax-reg-{bare}")
        return {"phone": phone}

    async def validate_two_factor_code(
        self, user: GatewayUser, code: str
    ) -> dict[str, Any] | None:
        bare = str(user.jid.bare)
        attempt = self._get_pending(bare)
        if attempt is None:
            raise XMPPError("not-acceptable", "No pending registration. Start over with 'register'.")

        code = (code or "").strip()
        if not code:
            raise XMPPError("bad-request", "Code is required.")
        if attempt.sms_done:
            raise XMPPError("not-acceptable", "SMS code already submitted. Continue the password form.")

        seen_password_requests = attempt.password.requests
        await attempt.sms.set_code(code)
        attempt.sms_done = True
        await self._wait_auth_progress(attempt, password_requests_above=seen_password_requests)
        log.info(
            "registration progress jid=%s ready=%s failed=%s password_requests=%s error=%s",
            bare,
            attempt.ready.is_set(),
            attempt.failed.is_set(),
            attempt.password.requests,
            attempt.error,
        )
        return await self._registration_result(bare, attempt, seen_password_requests)

    async def submit_max_password(self, user: GatewayUser, password: str) -> dict[str, Any] | None:
        bare = str(user.jid.bare)
        attempt = self._get_pending(bare)
        if attempt is None:
            raise XMPPError("not-acceptable", "No pending registration. Start over with 'register'.")

        password = (password or "").strip()
        if not password:
            raise XMPPError("bad-request", "Password is required.")

        seen_password_requests = attempt.password.requests
        await attempt.password.set_password(password)
        attempt.password.requested.clear()
        await self._wait_auth_progress(attempt, password_requests_above=seen_password_requests)
        log.info(
            "password progress jid=%s ready=%s failed=%s password_requests=%s error=%s",
            bare,
            attempt.ready.is_set(),
            attempt.failed.is_set(),
            attempt.password.requests,
            attempt.error,
        )
        return await self._registration_result(bare, attempt, seen_password_requests)

    async def _registration_result(
        self,
        bare: str,
        attempt: AuthAttempt,
        seen_password_requests: int,
    ) -> dict[str, Any]:
        if attempt.ready.is_set():
            return await self._finish_success(bare, attempt)

        if attempt.failed.is_set():
            err = attempt.error or "MAX login failed"
            await self._release_client(attempt)
            self._cleanup_pending(bare)
            raise XMPPError("not-acceptable", err)

        if attempt.password.requests > seen_password_requests:
            raise PasswordRequired(attempt.password.hint)

        await self._release_client(attempt)
        self._cleanup_pending(bare)
        raise XMPPError("remote-server-timeout", "MAX login did not finish.")

    async def _finish_success(self, bare: str, attempt: AuthAttempt) -> dict[str, Any]:
        data: dict[str, Any] = {"phone": attempt.phone}
        if attempt.max_user_id is not None:
            data["max_user_id"] = attempt.max_user_id
        # Release the session file before Slidge starts Session.login() on the same path.
        await self._release_client(attempt)
        self._cleanup_pending(bare)
        return data

    async def _wait_auth_progress(
        self,
        attempt: AuthAttempt,
        *,
        password_requests_above: int,
        timeout: float = 180.0,
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if attempt.ready.is_set() or attempt.failed.is_set():
                return
            if attempt.password.requests > password_requests_above:
                return
            if loop.time() > deadline:
                attempt.failed.set()
                attempt.error = "Registration timed out"
                return
            await asyncio.sleep(0.2)

    def _make_temp_client(
        self,
        bare_jid: str,
        phone: str,
        sms: QueueSmsProvider,
        pw: QueuePasswordProvider,
    ) -> Any:
        from pymax import Client

        work_dir = self.sessions_dir / session_dirname(bare_jid)
        work_dir.mkdir(parents=True, exist_ok=True)

        client = Client(
            phone=phone,
            work_dir=str(work_dir),
            session_name="session.db",
            sms_code_provider=sms,
            password_provider=pw,
        )

        @client.on_start()
        async def _on_start(c: Any) -> None:
            for att in list(self._pending.values()):
                if att.client is c or att.phone == phone:
                    att.max_user_id = int_or_none(c.me)
                    att.ready.set()
                    break

        return client

    async def _release_client(self, attempt: AuthAttempt) -> None:
        client = attempt.client
        attempt.client = None
        task = attempt.task
        if client is not None:
            try:
                await client.stop()
            except Exception:
                log.debug("failed to stop registration client", exc_info=True)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
