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

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from max_transport.jids import bare_jid, safe_filename


@dataclass(slots=True)
class RegisteredUser:
    jid: str
    phone: str
    max_user_id: int | None = None
    max_name: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegisteredUser:
        return cls(
            jid=bare_jid(str(data["jid"])),
            phone=str(data.get("phone") or ""),
            max_user_id=int(data["max_user_id"]) if data.get("max_user_id") is not None else None,
            max_name=data.get("max_name"),
            created_at=float(data.get("created_at") or 0.0),
            updated_at=float(data.get("updated_at") or 0.0),
        )


class UserStore:
    """One JSON file per registered XMPP account."""

    def __init__(self, users_dir: Path) -> None:
        self.users_dir = users_dir
        self.users_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, jid: str) -> Path:
        return self.users_dir / f"{safe_filename(jid)}.json"

    def get(self, jid: str) -> RegisteredUser | None:
        path = self._path(jid)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return RegisteredUser.from_dict(data)

    def list(self) -> list[RegisteredUser]:
        users: list[RegisteredUser] = []
        for path in sorted(self.users_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                users.append(RegisteredUser.from_dict(data))
            except (OSError, json.JSONDecodeError, KeyError, ValueError):
                continue
        return users

    def save(self, user: RegisteredUser) -> None:
        user.jid = bare_jid(user.jid)
        now = time.time()
        if not user.created_at:
            user.created_at = now
        user.updated_at = now
        path = self._path(user.jid)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(user.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)

    def delete(self, jid: str) -> bool:
        path = self._path(jid)
        if not path.is_file():
            return False
        path.unlink()
        return True


class MessageIdStore:
    """Bidirectional MAX ↔ XMPP message-id map.

    JSON-per-user would grow without bound as conversations continue, and
    edits need lookups in both directions. SQLite is the right store here.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS msgid (
                user_jid TEXT NOT NULL,
                max_chat_id INTEGER NOT NULL,
                max_msg_id INTEGER NOT NULL,
                xmpp_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (user_jid, max_msg_id)
            )
            """
        )
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_msgid_xmpp ON msgid(user_jid, xmpp_id)"
        )
        self._conn.commit()

    def put(self, user_jid: str, max_chat_id: int, max_msg_id: int, xmpp_id: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO msgid (user_jid, max_chat_id, max_msg_id, xmpp_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_jid, max_msg_id) DO UPDATE SET
                    max_chat_id=excluded.max_chat_id,
                    xmpp_id=excluded.xmpp_id
                """,
                (bare_jid(user_jid), int(max_chat_id), int(max_msg_id), xmpp_id, time.time()),
            )
            self._conn.commit()

    def xmpp_id_for(self, user_jid: str, max_msg_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT xmpp_id FROM msgid WHERE user_jid=? AND max_msg_id=?",
                (bare_jid(user_jid), int(max_msg_id)),
            ).fetchone()
        return str(row[0]) if row else None

    def max_ids_for(self, user_jid: str, xmpp_id: str) -> tuple[int, int] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT max_chat_id, max_msg_id FROM msgid WHERE user_jid=? AND xmpp_id=?",
                (bare_jid(user_jid), xmpp_id),
            ).fetchone()
        if not row:
            return None
        return int(row[0]), int(row[1])

    def last_outgoing(self, user_jid: str, max_chat_id: int) -> tuple[str, int] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT xmpp_id, max_msg_id FROM msgid
                WHERE user_jid=? AND max_chat_id=?
                ORDER BY created_at DESC LIMIT 1
                """,
                (bare_jid(user_jid), int(max_chat_id)),
            ).fetchone()
        if not row:
            return None
        return str(row[0]), int(row[1])

    def close(self) -> None:
        with self._lock:
            self._conn.close()
