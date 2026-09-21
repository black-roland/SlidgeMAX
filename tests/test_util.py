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

from max_transport.util import (
    display_name,
    int_or_none,
    is_group_chat,
    looks_like_incoming_call,
    parse_call_info,
    sender_id,
)


def test_int_or_none_and_sender() -> None:
    assert int_or_none(7) == 7
    assert int_or_none("8") == 8
    me = SimpleNamespace(contact=SimpleNamespace(id=11))
    assert int_or_none(me) == 11
    msg = SimpleNamespace(sender=5)
    assert sender_id(msg) == 5
    msg2 = SimpleNamespace(sender=SimpleNamespace(id=9))
    assert sender_id(msg2) == 9


def test_display_name() -> None:
    user = SimpleNamespace(
        id=1,
        names=[SimpleNamespace(name=None, first_name="Ada", last_name="Lovelace")],
    )
    assert display_name(user) == "Ada Lovelace"
    user2 = SimpleNamespace(id=2, names=[SimpleNamespace(name="Bob")])
    assert display_name(user2) == "Bob"


def test_display_name_with_enriched_user() -> None:
    from pymax.types.domain import Name, User

    user = User(id=1, names=[Name(name=None, first_name="Ada", last_name="Lovelace")])
    assert display_name(user) == "Ada Lovelace"
    user2 = User(id=2, names=[Name(name="Bob")])
    assert display_name(user2) == "Bob"
    user3 = User(id=3)
    assert display_name(user3, fallback="MAX 3") == "MAX 3"


def test_group_chat_detection() -> None:
    dialog = SimpleNamespace(type=SimpleNamespace(name="DIALOG"))
    group = SimpleNamespace(type=SimpleNamespace(name="CHAT"))
    channel = SimpleNamespace(type="CHANNEL")
    assert is_group_chat(dialog) is False
    assert is_group_chat(group) is True
    assert is_group_chat(channel) is True


def test_incoming_call_opcode() -> None:
    assert looks_like_incoming_call(137, {}) is True
    assert looks_like_incoming_call("NOTIF_CALL_START", {}) is True
    payload = {"callerId": 15, "isVideo": True, "chatId": 99}
    assert looks_like_incoming_call(0, payload) is True
    info = parse_call_info(payload)
    assert info["caller_id"] == 15
    assert info["video"] is True
