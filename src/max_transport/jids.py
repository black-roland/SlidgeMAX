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

from __future__ import annotations

import re
from dataclasses import dataclass

_UNSAFE = re.compile(r"[^a-zA-Z0-9._-]+")


@dataclass(frozen=True, slots=True)
class ContactAddress:
    user_id: int
    jid: str


def component_jid(component: str) -> str:
    return component.strip().lower()


def bare_jid(jid: str) -> str:
    value = (jid or "").strip()
    if "/" in value:
        value = value.split("/", 1)[0]
    return value.lower()


def jid_domain(jid: str) -> str:
    bare = bare_jid(jid)
    if "@" not in bare:
        return bare
    return bare.split("@", 1)[1]


def contact_jid(user_id: int, component: str) -> str:
    return f"{int(user_id)}@{component_jid(component)}"


def parse_contact_localpart(localpart: str) -> int | None:
    if not localpart or not localpart.isdigit():
        return None
    try:
        value = int(localpart)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def parse_contact_jid(jid: str, component: str) -> int | None:
    bare = bare_jid(jid)
    if "@" not in bare:
        return parse_contact_localpart(bare)
    local, domain = bare.split("@", 1)
    if domain != component_jid(component):
        return None
    return parse_contact_localpart(local)


def safe_filename(jid: str) -> str:
    """Stable filesystem name for a bare JID."""
    bare = bare_jid(jid)
    return _UNSAFE.sub("_", bare.replace("@", "_at_")) or "user"
