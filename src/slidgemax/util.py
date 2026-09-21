from __future__ import annotations

import re
from typing import Any

_PHONE_STRIP = re.compile(r"[\s\-().]")


def session_dirname(key: str) -> str:
    """Stable directory name for a JID or phone under max-sessions/."""
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", key.replace("@", "_at_")) or "user"


def normalize_phone(raw: str) -> str:
    """Normalize a phone number to a +E.164-ish form MAX will accept."""
    s = _PHONE_STRIP.sub("", (raw or "").strip())
    if not s:
        raise ValueError("phone number is empty")
    if s.startswith("00"):
        s = "+" + s[2:]
    if s.startswith("8") and len(s) == 11 and s[1:].isdigit():
        s = "+7" + s[1:]
    elif s.startswith("7") and len(s) == 11 and s.isdigit():
        s = "+" + s
    elif not s.startswith("+"):
        if not s.isdigit():
            raise ValueError(f"invalid phone number: {raw!r}")
        s = "+" + s
    if not s[1:].isdigit():
        raise ValueError(f"invalid phone number: {raw!r}")
    if len(s) < 8:
        raise ValueError(f"phone number too short: {raw!r}")
    return s


def int_or_none(value: Any) -> int | None:
    if value is None or value is False:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    ident = getattr(value, "id", None)
    if isinstance(ident, int):
        return ident
    contact = getattr(value, "contact", None)
    if contact is not None:
        cid = getattr(contact, "id", None)
        if isinstance(cid, int):
            return cid
    return None


def sender_id(message: Any) -> int | None:
    return int_or_none(getattr(message, "sender", None))


def dialog_chat_id(first_user_id: int, second_user_id: int) -> int:
    """MAX computes 1:1 chat IDs as XOR of the two user IDs."""
    return first_user_id ^ second_user_id


def display_name(user: Any, fallback: str | None = None) -> str:
    if user is None:
        return fallback or "MAX user"
    names = getattr(user, "names", None) or []
    if names:
        n = names[0]
        full = getattr(n, "name", None)
        if full:
            return str(full)
        parts = [
            getattr(n, "first_name", None),
            getattr(n, "last_name", None),
        ]
        joined = " ".join(p for p in parts if p)
        if joined:
            return joined
    for attr in ("name", "first_name", "nick"):
        val = getattr(user, attr, None)
        if val:
            return str(val)
    ident = int_or_none(user)
    if ident is not None:
        return fallback or f"MAX {ident}"
    return fallback or "MAX user"


def attachment_type_name(attach: Any) -> str:
    t = getattr(attach, "type", None)
    if t is not None:
        name = getattr(t, "name", None) or str(t)
        return str(name).upper()
    return type(attach).__name__.upper()


def is_call_attachment(attach: Any) -> bool:
    name = attachment_type_name(attach)
    return "CALL" in name


def is_text_attachment_only(message: Any) -> bool:
    attaches = list(getattr(message, "attaches", None) or [])
    if not attaches:
        return True
    return all(attachment_type_name(a) in {"", "CONTROL", "NONE"} for a in attaches)


# MAX protocol: NOTIF_INCOMING_CALL
INCOMING_CALL_OPCODE = 137


def payload_as_dict(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return payload
    dump = getattr(payload, "model_dump", None)
    if callable(dump):
        try:
            return dump(by_alias=True)
        except TypeError:
            return dump()
    as_dict = getattr(payload, "dict", None)
    if callable(as_dict):
        return as_dict()
    extra = getattr(payload, "model_extra", None)
    if isinstance(extra, dict):
        return extra
    return {}


def opcode_name(opcode: Any) -> str:
    if opcode is None:
        return ""
    name = getattr(opcode, "name", None)
    if name:
        return str(name).upper()
    value = getattr(opcode, "value", opcode)
    return str(value).upper()


def opcode_value(opcode: Any) -> int | None:
    if opcode is None:
        return None
    if isinstance(opcode, int):
        return opcode
    value = getattr(opcode, "value", None)
    if isinstance(value, int):
        return value
    if isinstance(opcode, str) and opcode.isdigit():
        return int(opcode)
    return None


def looks_like_incoming_call(opcode: Any, payload: dict[str, Any]) -> bool:
    value = opcode_value(opcode)
    name = opcode_name(opcode)
    if value == INCOMING_CALL_OPCODE:
        return True
    if "INCOMING_CALL" in name or "CALL_START" in name or name in {"NOTIF_INCOMING_CALL", "NOTIF_CALL_START"}:
        return True
    if not payload:
        return False
    keys = {str(k).lower() for k in payload}
    if "incomingcall" in keys or "isvideo" in keys and ("callerid" in keys or "initiatorid" in keys):
        return True
    nested = payload.get("call") or payload.get("incomingCall") or payload.get("incoming_call")
    return isinstance(nested, dict)


def parse_call_info(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("call") or payload.get("incomingCall") or payload.get("incoming_call")
    if not isinstance(data, dict):
        data = payload
    caller = (
        data.get("callerId")
        or data.get("caller_id")
        or data.get("initiatorId")
        or data.get("initiator_id")
        or data.get("userId")
        or data.get("user_id")
        or data.get("contactId")
        or data.get("contact_id")
    )
    chat_id = data.get("chatId") or data.get("chat_id") or data.get("conversationId") or data.get(
        "conversation_id"
    )
    video = data.get("isVideo")
    if video is None:
        video = data.get("video")
    if video is None:
        kind = str(data.get("type") or data.get("callType") or "").upper()
        video = "VIDEO" in kind
    return {
        "caller_id": int_or_none(caller),
        "chat_id": int_or_none(chat_id),
        "video": bool(video),
        "name": data.get("name") or data.get("callerName") or data.get("title"),
    }
