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

"""PyMax client wrapper that will not relogin on its own.

PyMax 2.4.1 ``Client.start()`` deletes a revoked session and calls
``request_code()``, then reconnect can repeat that until MAX rate-limits the
phone. This wrapper keeps network reconnect and stops on a dead token.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pymax import Client
from pymax.exceptions import ApiError
from pymax.protocol.enums import Opcode
from pymax.session.store import SessionStore

if TYPE_CHECKING:
    from pymax.app import App
    from pymax.auth.models import AuthResult

log = logging.getLogger(__name__)

REVOKED_LOGIN_MESSAGE = (
    "MAX login token was revoked. Register again. "
    "This gateway will not request an SMS code on its own."
)

_REVOKED_ERRORS = ("FAIL_LOGIN_TOKEN", "FAIL_LOGOUT_ALL")


class LoginTokenRevoked(RuntimeError):
    """Saved MAX login token can no longer be used."""


class RefuseSmsAuth:
    """Auth flow that fails before ``request_code()``.

    Used for an already registered session. A missing or deleted session file
    must not fall through to ``ConsoleSmsCodeProvider``.
    """

    async def authenticate(self, app: App) -> AuthResult:
        raise LoginTokenRevoked(REVOKED_LOGIN_MESSAGE)


def is_revoked_login_token(exc: BaseException) -> bool:
    return (
        isinstance(exc, ApiError)
        and exc.opcode == Opcode.LOGIN
        and any(err in (exc.error, exc.message) for err in _REVOKED_ERRORS)
    )


class MaxClient(Client):
    """``Client`` whose ``start()`` does not relogin or tight-loop on a dead token."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._stop_requested = False
        super().__init__(*args, **kwargs)

    async def stop(self) -> None:
        self._stop_requested = True
        await super().stop()

    async def start(self) -> None:
        while not self._stop_requested:
            try:
                await self.connect()
                if not self.is_connected:
                    return
                await self._connection.wait_closed()
            except asyncio.CancelledError:
                await self.close()
                raise
            except LoginTokenRevoked:
                await self.close()
                raise
            except ApiError as exc:
                await self.close()
                if is_revoked_login_token(exc):
                    log.error("MAX login token revoked; not requesting a new SMS code")
                    raise LoginTokenRevoked(REVOKED_LOGIN_MESSAGE) from exc
                raise
            except (ConnectionError, EOFError, OSError, TimeoutError):
                await self.close()
                if self._stop_requested or not self.extra_config.reconnect:
                    raise
                log.info(
                    "MAX connection failed; reconnecting in %s seconds",
                    self.extra_config.reconnect_delay,
                )
                await asyncio.sleep(self.extra_config.reconnect_delay)
                if self._stop_requested:
                    return
                self._reset_runtime()
            except Exception:
                await self.close()
                raise
            else:
                await self.close()
                return


async def discard_saved_session(client: Client) -> None:
    """Delete the local MAX session so the next login can use SMS.

    Call this only from an interactive registration, and only once.
    """
    await client.close()
    store = SessionStore(client.work_dir, client.session_name)
    try:
        await store.delete_all_sessions()
    finally:
        await store.close()
    if getattr(client, "_config", None) is not None:
        client._reset_runtime()


async def start_once_or_recover(
    client: MaxClient,
    recover: Callable[[], bool],
) -> None:
    """Run ``start()``. On a revoked token, drop the saved session and try once more.

    ``recover`` is checked when the token failure happens. A long-lived client
    that already logged in must pass a callback that returns false.
    """
    try:
        await client.start()
    except LoginTokenRevoked:
        if not recover():
            raise
        log.warning("MAX login token revoked during registration; requesting SMS once")
        await discard_saved_session(client)
        await client.start()
