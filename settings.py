"""Конфигурация: config.toml (настройки) + .env (секреты)."""

from __future__ import annotations

import os
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.toml"

_SECRET_KEYS = {
    "proxy_string": "PROXY_STRING",
    "socks5_proxy": "SOCKS5_PROXY",
    "proxy_change_url": "PROXY_CHANGE_URL",
    "cookies_api_key": "COOKIES_API_KEY",
}


def _load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_env() -> None:
    _load_dotenv()


def load_config() -> dict:
    _load_dotenv()
    if not CONFIG_PATH.is_file():
        raise FileNotFoundError(
            f"Не найден {CONFIG_PATH.name}. Скопируйте config.toml.example и заполните .env"
        )
    with CONFIG_PATH.open("rb") as fh:
        cfg = dict(tomllib.load(fh)["avito"])
    for cfg_key, env_key in _SECRET_KEYS.items():
        env_val = os.environ.get(env_key, "").strip()
        if env_val:
            cfg[cfg_key] = env_val
        else:
            cfg[cfg_key] = (cfg.get(cfg_key) or "").strip()
    web_port = os.environ.get("WEB_PORT", "").strip()
    if web_port.isdigit():
        cfg["web_port"] = int(web_port)
    return cfg
