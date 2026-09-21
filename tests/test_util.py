from slidgemax.util import (
    dialog_chat_id,
    display_name,
    int_or_none,
    normalize_phone,
)


def test_normalize_phone() -> None:
    assert normalize_phone("8 (999) 000-00-00") == "+79990000000"
    assert normalize_phone("79990000000") == "+79990000000"
    assert normalize_phone("+44 7700 900123") == "+447700900123"
    assert normalize_phone("00447700900123") == "+447700900123"


def test_int_or_none() -> None:
    assert int_or_none(42) == 42
    assert int_or_none("123") == 123
    assert int_or_none(None) is None


def test_dialog_xor() -> None:
    me, peer = 10, 25
    chat = dialog_chat_id(me, peer)
    assert (chat ^ me) == peer
    assert (chat ^ peer) == me


def test_display_name() -> None:
    class N:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class U:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    u = U(names=[N(first_name="Ada", last_name="Lovelace")])
    assert display_name(u) == "Ada Lovelace"
    u2 = U(id=99)
    assert "MAX" in display_name(u2)
