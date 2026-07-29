"""Кэш ответов по символам + журналирование запросов и ответов.

Кэш отдельный для каждого символа (EURUSD и XAUUSD не смешиваются).
Каждый запрос советника и каждый ответ/ошибка пишутся в JSONL-журнал —
эти журналы можно использовать для режима воспроизведения (replay).
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from config import BASE_DIR, CONFIG

_lock = threading.Lock()
_cache: dict[str, dict] = {}  # symbol -> {"data": ..., "saved_at": ...}


def log_event(event: dict) -> None:
    """Запись события в журнал logs/bridge-YYYYMMDD.jsonl."""
    log_dir = BASE_DIR / CONFIG["logging"]["dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    path = log_dir / f"bridge-{datetime.now(timezone.utc):%Y%m%d}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def cache_get(symbol: str) -> tuple[dict | None, float]:
    """Возвращает (ответ, возраст в секундах) или (None, 0)."""
    with _lock:
        entry = _cache.get(symbol)
        if entry is None:
            return None, 0.0
        return entry["data"], time.time() - entry["saved_at"]


def cache_put(symbol: str, data: dict) -> None:
    with _lock:
        _cache[symbol] = {"data": data, "saved_at": time.time()}


def cache_fresh(symbol: str) -> dict | None:
    """Свежий ответ из кэша (моложе ttl_minutes) или None."""
    data, age = cache_get(symbol)
    if data is not None and age < CONFIG["cache"]["ttl_minutes"] * 60:
        return data
    return None


def load_replay_response(symbol: str) -> dict | None:
    """Режим воспроизведения: последний валидный записанный ответ для символа."""
    replay = CONFIG["replay"]
    if not replay["enabled"] or not replay["file"]:
        return None
    path = Path(replay["file"])
    if not path.is_absolute():
        path = BASE_DIR / path
    if not path.exists():
        return None
    last = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("symbol") == symbol and event.get("valid") and event.get("response"):
                last = event["response"]
    return last
