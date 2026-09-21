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

from types import SimpleNamespace
from unittest.mock import AsyncMock

from max_transport.config import config_from_dict
from max_transport.session import MaxSession
from max_transport.store import RegisteredUser


def _session() -> MaxSession:
    cfg = config_from_dict({"component": {"jid": "max.example.org", "secret": "x"}})
    user = RegisteredUser(jid="alice@example.org", phone="+7999", max_user_id=10)
    session = MaxSession(cfg, user)
    session.client = SimpleNamespace(
        me=SimpleNamespace(contact=SimpleNamespace(id=10)),
        chats=[],
        dialogs=[],
        contacts=[],
    )
    return session


def test_resolve_dialog_from_xor() -> None:
    session = _session()
    # chat_id = 10 ^ 25 = 19
    message = SimpleNamespace(chat_id=19, sender=25)
    assert session.resolve_dialog_peer(message) == 25


def test_ignore_group_when_sender_mismatches_xor() -> None:
    session = _session()
    # group chat id that does not XOR to the sender
    message = SimpleNamespace(chat_id=999_001, sender=25)
    assert session.resolve_dialog_peer(message) is None


def test_echo_from_self_still_maps_peer() -> None:
    session = _session()
    message = SimpleNamespace(chat_id=19, sender=10)
    assert session.resolve_dialog_peer(message) == 25


async def test_lookup_users_returns_dict_keyed_by_id() -> None:
    session = _session()
    user1 = SimpleNamespace(id=1, names=[SimpleNamespace(name="Ada Lovelace")])
    user2 = SimpleNamespace(id=2, names=[SimpleNamespace(name="Bob")])
    session.client.get_users = AsyncMock(return_value=[user1, user2])
    result = await session.lookup_users([1, 2])
    assert result == {1: user1, 2: user2}
    assert result[1].id == 1
    assert result[2].id == 2


async def test_lookup_users_skips_missing_users() -> None:
    session = _session()
    user1 = SimpleNamespace(id=1, names=[SimpleNamespace(name="Ada Lovelace")])
    user3 = SimpleNamespace(id=3, names=[SimpleNamespace(name="Charlie")])
    session.client.get_users = AsyncMock(return_value=[user1, user3])
    result = await session.lookup_users([1, 2, 3])
    assert result == {1: user1, 3: user3}
    assert 2 not in result


async def test_lookup_users_no_client() -> None:
    session = _session()
    session.client = None
    assert await session.lookup_users([1, 2]) == {}


async def test_lookup_users_no_get_users_method() -> None:
    session = _session()
    assert await session.lookup_users([1, 2]) == {}


async def test_lookup_users_handles_exception() -> None:
    session = _session()
    session.client.get_users = AsyncMock(side_effect=RuntimeError("server error"))
    assert await session.lookup_users([1, 2]) == {}


async def test_lookup_users_empty_input() -> None:
    session = _session()
    session.client.get_users = AsyncMock(return_value=[])
    assert await session.lookup_users([]) == {}
