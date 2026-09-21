from __future__ import annotations

from pathlib import Path

import pytest

from max_transport.config import config_from_dict, load_config


def test_load_example(tmp_path: Path) -> None:
    src = Path(__file__).resolve().parents[1] / "config.example.toml"
    text = src.read_text(encoding="utf-8").replace("change-me", "secret-value")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(text, encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg.component.jid == "max.example.org"
    assert cfg.component.secret == "secret-value"
    assert cfg.component.port == 5347
    assert cfg.max.device_type == "DESKTOP"
    assert cfg.bridge.always_online is True
    assert cfg.data_dir == (tmp_path / "data").resolve()


def test_env_expansion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAX_COMPONENT_SECRET", "from-env")
    raw = {
        "component": {"jid": "max.example.org", "secret": "${MAX_COMPONENT_SECRET}"},
        "storage": {"data_dir": str(tmp_path / "store")},
    }
    cfg = config_from_dict(raw, source=tmp_path / "x.toml")
    assert cfg.component.secret == "from-env"


def test_registration_acl() -> None:
    cfg = config_from_dict(
        {
            "component": {"jid": "max.example.org", "secret": "x"},
            "registration": {"allowed_domains": ["example.org"]},
        }
    )
    assert cfg.registration_allowed("alice@example.org")
    assert not cfg.registration_allowed("bob@other.org")
    cfg.registration.allowed_jids = ["alice@example.org"]
    assert cfg.registration_allowed("alice@example.org")
    assert not cfg.registration_allowed("eve@example.org")


def test_missing_secret() -> None:
    with pytest.raises(ValueError, match="secret"):
        config_from_dict({"component": {"jid": "max.example.org"}})


def test_bad_device_type() -> None:
    with pytest.raises(ValueError, match="device_type"):
        config_from_dict(
            {
                "component": {"jid": "max.example.org", "secret": "x"},
                "max": {"device_type": "WEB"},
            }
        )
