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

"""Publish legacy avatars without blocking roster fill."""

from __future__ import annotations

from typing import TYPE_CHECKING

from slidge.core.mixins import AvatarMixin
from slidge.util.types import Avatar

from .util import avatar_https_url, avatar_legacy_id

if TYPE_CHECKING:
    from .session import Session


class SetAvatarMixin(AvatarMixin):
    session: Session

    async def update_avatar(self, base_url: str | None, photo_id: int | None) -> None:
        unique_id = avatar_legacy_id(photo_id)
        url = avatar_https_url(base_url)
        cached = self.avatar
        if unique_id is not None and cached is not None and cached.unique_id == unique_id:
            return
        if unique_id is None or url is None:
            if unique_id is None and url is None and cached is not None:
                self.avatar = None
            return
        self.avatar = Avatar(url=url, unique_id=unique_id)
