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

from pathlib import Path

from max_transport.store import MessageIdStore, RegisteredUser, UserStore


def test_user_store_roundtrip(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "users")
    user = RegisteredUser(jid="Alice@Example.Org/res", phone="+79990000000", max_user_id=42, max_name="Alice")
    store.save(user)
    loaded = store.get("alice@example.org")
    assert loaded is not None
    assert loaded.jid == "alice@example.org"
    assert loaded.phone == "+79990000000"
    assert loaded.max_user_id == 42
    assert [u.jid for u in store.list()] == ["alice@example.org"]
    assert store.delete("alice@example.org") is True
    assert store.get("alice@example.org") is None


def test_msgid_store(tmp_path: Path) -> None:
    store = MessageIdStore(tmp_path / "msgids.sqlite")
    store.put("user@x", 99, 1001, "orig-1")
    assert store.xmpp_id_for("user@x", 1001) == "orig-1"
    assert store.max_ids_for("USER@x", "orig-1") == (99, 1001)
    store.put("user@x", 99, 1002, "orig-2")
    last = store.last_outgoing("user@x", 99)
    assert last is not None
    assert last[0] == "orig-2"
    store.close()
