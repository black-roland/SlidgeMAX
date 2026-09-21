from types import SimpleNamespace

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
