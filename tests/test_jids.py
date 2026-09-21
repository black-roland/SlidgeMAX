from max_transport.jids import (
    bare_jid,
    contact_jid,
    parse_contact_jid,
    parse_contact_localpart,
    safe_filename,
)
from max_transport.util import dialog_chat_id, dialog_peer_id, normalize_phone


def test_contact_jid_roundtrip() -> None:
    jid = contact_jid(123456, "max.example.org")
    assert jid == "123456@max.example.org"
    assert parse_contact_jid(jid, "max.example.org") == 123456
    assert parse_contact_jid("123456@max.example.org/resource", "MAX.EXAMPLE.ORG") == 123456
    assert parse_contact_jid("123456@other.org", "max.example.org") is None
    assert parse_contact_localpart("00") is None
    assert parse_contact_localpart("abc") is None


def test_bare_and_filename() -> None:
    assert bare_jid("Alice@Example.Org/phone") == "alice@example.org"
    name = safe_filename("Alice@Example.Org")
    assert "@" not in name
    assert "alice" in name


def test_dialog_xor() -> None:
    me, peer = 10, 25
    chat = dialog_chat_id(me, peer)
    assert dialog_peer_id(chat, me) == peer
    assert dialog_peer_id(chat, peer) == me
    assert dialog_chat_id(peer, me) == chat


def test_normalize_phone() -> None:
    assert normalize_phone("8 (999) 000-00-00") == "+79990000000"
    assert normalize_phone("79990000000") == "+79990000000"
    assert normalize_phone("+44 7700 900123") == "+447700900123"
    assert normalize_phone("00447700900123") == "+447700900123"
