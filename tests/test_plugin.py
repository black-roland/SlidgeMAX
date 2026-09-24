from __future__ import annotations

from slidge.command.register import RegistrationType
from slidge.group import LegacyBookmarks

from slidgemax import Gateway, Session, __version__
from slidgemax.contact import Contact, Roster
from slidgemax.session import max_extra_config
from slidgemax.util import normalize_phone


def test_version() -> None:
    assert __version__


def test_generic_wiring() -> None:
    assert Roster.contact_cls is Contact
    assert Session.roster_cls is Roster
    assert Session.bookmarks_cls is LegacyBookmarks
    assert Gateway.session_cls is Session


def test_gateway_identity() -> None:
    assert Gateway.COMPONENT_TYPE == "max"
    assert Gateway.REGISTRATION_TYPE == RegistrationType.TWO_FACTOR_CODE
    assert Gateway.GROUPS is False
    vars_ = {field.var for field in Gateway.REGISTRATION_FIELDS}
    assert vars_ == {"phone", "password"}
    assert [field.var for field in Gateway.SEARCH_FIELDS] == ["query"]


def test_contact_disco_flags() -> None:
    assert Contact.CORRECTION is True
    assert Contact.REACTION is False
    assert Contact.UPLOAD is False
    assert Contact.AVATAR is False


def test_phone_used_by_registration() -> None:
    assert normalize_phone("89991234567") == "+79991234567"


def test_login_requests_full_contact_sync() -> None:
    assert max_extra_config().sync.contacts_sync == -1
