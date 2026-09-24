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

"""Plugin-specific options, following the Slidge config convention.

Upper-case names become CLI flags (`--device-type`), INI keys (`device-type`)
and environment variables (`SLIDGE_SLIDGEMAX_DEVICE_TYPE`).
"""

from __future__ import annotations

DEVICE_TYPE: str = "ANDROID"
DEVICE_TYPE__DOC = (
    "Device type advertised to MAX during handshake. One of: DESKTOP, ANDROID, IOS. "
)

RECONNECT: bool = True
RECONNECT__DOC = (
    "Automatically reconnect the MAX TCP session after a network error. "
    "Pass --reconnect to disable (Slidge inverts boolean flags whose default is true)."
)

RECONNECT_DELAY: float = 3.0
RECONNECT_DELAY__DOC = "Seconds to wait before reconnecting a dropped MAX session."

IGNORE_GROUPS: bool = True
IGNORE_GROUPS__DOC = (
    "Drop group and channel messages. Groups are out of scope for this gateway."
)

CALL_NOTIFICATIONS: bool = True
CALL_NOTIFICATIONS__DOC = (
    "Turn incoming MAX calls into a plain-text XMPP notice. Calls themselves are not bridged."
)

CALL_VOICE_TEXT: str = "Incoming voice call"
CALL_VOICE_TEXT__DOC = "Body of the XMPP notice sent for an incoming voice call."

CALL_VIDEO_TEXT: str = "Incoming video call"
CALL_VIDEO_TEXT__DOC = "Body of the XMPP notice sent for an incoming video call."

PLACEHOLDER_UNSUPPORTED: bool = True
PLACEHOLDER_UNSUPPORTED__DOC = (
    "When MAX sends non-text content this gateway does not bridge, send a placeholder "
    "text message instead of dropping the event silently."
)

UNSUPPORTED_TEXT: str = "Unsupported MAX content (not bridged)."
UNSUPPORTED_TEXT__DOC = "Placeholder body used when PLACEHOLDER_UNSUPPORTED is enabled."

REGISTRATION_TIMEOUT: int = 120
REGISTRATION_TIMEOUT__DOC = (
    "How long to wait, in seconds, for SMS / 2FA during registration before giving up."
)

MAX_HOST: str = "api2.oneme.ru"
MAX_HOST__DOC = "MAX TCP API host."

MAX_PORT: int = 443
MAX_PORT__DOC = "MAX TCP API port."

MAX_USE_SSL: bool = True
MAX_USE_SSL__DOC = "Use TLS for the MAX TCP connection."
