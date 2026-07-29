"""Чтение конфигурации моста: config.toml + .env (ключ API)."""

import os
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

DEFAULTS = {
    "server": {"host": "127.0.0.1", "port": 8080},
    "anthropic": {"model": "claude-sonnet-5", "max_tokens": 512, "timeout_seconds": 30},
    "cache": {"ttl_minutes": 25},
    "mock": {
        "enabled": False,
        "regime": "range",
        "trade_allowed": True,
        "risk_multiplier": 0.5,
        "reason": "мок-ответ для тестирования",
    },
    "replay": {"enabled": False, "file": ""},
    "symbols": {
        "eurusd": "EURUSD",
        "xauusd": "XAUUSD",
        "dxy_candidates": ["DXY", "USDX", "DX", "USDIDX", "USIDX", "DXY.cash"],
        "us10y_candidates": ["US10Y", "UST10Y", "USTN10", "US10YR", "US10Y.cash"],
    },
    "calendar": {
        "enabled": True,
        "url": "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    },
    "logging": {"dir": "logs"},
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config() -> dict:
    load_dotenv(BASE_DIR / ".env")

    cfg = DEFAULTS
    path = BASE_DIR / "config.toml"
    if path.exists():
        with open(path, "rb") as f:
            cfg = _merge(DEFAULTS, tomllib.load(f))
    else:
        print(
            "ВНИМАНИЕ: config.toml не найден, используются значения по умолчанию. "
            "Скопируйте config.example.toml в config.toml.",
            file=sys.stderr,
        )

    # Безопасность: мост слушает только локальный адрес
    if cfg["server"]["host"] != "127.0.0.1":
        print(
            "ВНИМАНИЕ: host принудительно заменён на 127.0.0.1 — "
            "мост не должен быть доступен из сети.",
            file=sys.stderr,
        )
        cfg["server"]["host"] = "127.0.0.1"

    cfg["anthropic"]["api_key"] = os.environ.get("ANTHROPIC_API_KEY", "")
    return cfg


CONFIG = load_config()
