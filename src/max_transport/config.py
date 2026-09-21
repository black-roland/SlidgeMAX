from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    raw = data.get(name) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"[{name}] must be a table")
    return raw


@dataclass(slots=True)
class ComponentConfig:
    jid: str
    secret: str
    server: str = "127.0.0.1"
    port: int = 5347
    name: str = "MAX Gateway"
    use_jabber_client_ns: bool = False


@dataclass(slots=True)
class StorageConfig:
    data_dir: Path = Path("./data")


@dataclass(slots=True)
class MaxConfig:
    device_type: str = "DESKTOP"
    reconnect: bool = True
    reconnect_delay_seconds: float = 3.0


@dataclass(slots=True)
class BridgeConfig:
    always_online: bool = True
    sync_contacts_on_login: bool = True
    auto_subscribe: bool = True
    ignore_groups: bool = True
    call_notifications: bool = True
    call_voice_text: str = "Incoming voice call"
    call_video_text: str = "Incoming video call"
    placeholder_unsupported: bool = True
    unsupported_text: str = "Unsupported MAX content (not bridged)."
    registration_timeout_seconds: float = 120.0


@dataclass(slots=True)
class RegistrationConfig:
    allowed_domains: list[str] = field(default_factory=list)
    allowed_jids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LoggingConfig:
    level: str = "INFO"
    file: str = ""


@dataclass(slots=True)
class Config:
    component: ComponentConfig
    storage: StorageConfig
    max: MaxConfig
    bridge: BridgeConfig
    registration: RegistrationConfig
    logging: LoggingConfig
    source: Path | None = None

    @property
    def data_dir(self) -> Path:
        return self.storage.data_dir

    @property
    def users_dir(self) -> Path:
        return self.data_dir / "users"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def msgid_path(self) -> Path:
        return self.data_dir / "msgids.sqlite"

    def ensure_dirs(self) -> None:
        self.users_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def registration_allowed(self, bare_jid: str) -> bool:
        jid = bare_jid.lower()
        domain = jid.split("@", 1)[-1] if "@" in jid else ""
        if self.registration.allowed_jids:
            allowed = {j.lower() for j in self.registration.allowed_jids}
            if jid not in allowed:
                return False
        if self.registration.allowed_domains:
            allowed_d = {d.lower() for d in self.registration.allowed_domains}
            if domain not in allowed_d:
                return False
        return True


def load_config(path: str | Path) -> Config:
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"config not found: {config_path}")
    with config_path.open("rb") as fh:
        raw = _expand(tomllib.load(fh))
    return config_from_dict(raw, source=config_path)


def config_from_dict(raw: dict[str, Any], source: Path | None = None) -> Config:
    raw = _expand(raw)
    comp = _section(raw, "component")
    jid = str(comp.get("jid") or "").strip()
    secret = str(comp.get("secret") or "")
    if not jid:
        raise ValueError("component.jid is required")
    if not secret:
        raise ValueError("component.secret is required")

    storage = _section(raw, "storage")
    data_dir = Path(str(storage.get("data_dir") or "./data"))
    if not data_dir.is_absolute():
        base = source.parent if source is not None else Path.cwd()
        data_dir = (base / data_dir).resolve()

    max_sec = _section(raw, "max")
    device = str(max_sec.get("device_type") or "DESKTOP").upper()
    if device not in {"DESKTOP", "ANDROID", "IOS"}:
        raise ValueError("max.device_type must be DESKTOP, ANDROID, or IOS")

    bridge = _section(raw, "bridge")
    reg = _section(raw, "registration")
    log = _section(raw, "logging")

    cfg = Config(
        component=ComponentConfig(
            jid=jid,
            secret=secret,
            server=str(comp.get("server") or "127.0.0.1"),
            port=int(comp.get("port") or 5347),
            name=str(comp.get("name") or "MAX Gateway"),
            use_jabber_client_ns=bool(comp.get("use_jabber_client_ns") or False),
        ),
        storage=StorageConfig(data_dir=data_dir),
        max=MaxConfig(
            device_type=device,
            reconnect=bool(max_sec.get("reconnect", True)),
            reconnect_delay_seconds=float(max_sec.get("reconnect_delay_seconds") or 3.0),
        ),
        bridge=BridgeConfig(
            always_online=bool(bridge.get("always_online", True)),
            sync_contacts_on_login=bool(bridge.get("sync_contacts_on_login", True)),
            auto_subscribe=bool(bridge.get("auto_subscribe", True)),
            ignore_groups=bool(bridge.get("ignore_groups", True)),
            call_notifications=bool(bridge.get("call_notifications", True)),
            call_voice_text=str(bridge.get("call_voice_text") or "Incoming voice call"),
            call_video_text=str(bridge.get("call_video_text") or "Incoming video call"),
            placeholder_unsupported=bool(bridge.get("placeholder_unsupported", True)),
            unsupported_text=str(
                bridge.get("unsupported_text") or "Unsupported MAX content (not bridged)."
            ),
            registration_timeout_seconds=float(
                bridge.get("registration_timeout_seconds") or 120.0
            ),
        ),
        registration=RegistrationConfig(
            allowed_domains=[str(x) for x in (reg.get("allowed_domains") or [])],
            allowed_jids=[str(x) for x in (reg.get("allowed_jids") or [])],
        ),
        logging=LoggingConfig(
            level=str(log.get("level") or "INFO").upper(),
            file=str(log.get("file") or ""),
        ),
        source=source,
    )
    return cfg
