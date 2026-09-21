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
