"""runtime_events.py — короткая память программы о том, что с ней случилось.

ЗАЧЕМ
Владелец несколько раз писал одно и то же: «он останавливается в работе по
истечении времени, я перезапустил — и сделки пошли». Каждый раз это
приходилось разбирать заново по коду, потому что от самой программы не
оставалось никаких следов: всё уходило в scalper.log, который никто не
открывает, а окно показывало ровно то же «Работает».

Здесь программа коротко записывает СОБЫТИЯ, которые объясняют молчание:
обрыв связи с терминалом, ошибку в цикле, срабатывание сторожа,
переподключение. Эти записи видны прямо на вкладке «Обзор» и лежат
файлом рядом с программой — их можно переслать, не разбираясь в логах.

ЧЕГО ЗДЕСЬ НЕТ
Это не замена журналу: сюда попадает только то, что человек должен увидеть
без подсказки. Полная картина по-прежнему в scalper.log.
"""

import json
import logging
import os
import sys
import threading
from datetime import datetime

log = logging.getLogger("runtime_events")

# Держим немного: это лента «что случилось недавно», а не архив.
MAX_EVENTS = 40
FILE_NAME = "runtime_events.json"

_lock = threading.Lock()
_events: list = []


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def path() -> str:
    return os.path.join(app_dir(), FILE_NAME)


def record(kind: str, text: str) -> dict:
    """Записать событие. kind — короткая метка ("сторож", "связь", "ошибка")."""
    event = {
        "at": datetime.now().strftime("%d.%m %H:%M:%S"),
        "kind": str(kind or "событие"),
        "text": str(text or "").strip(),
    }
    with _lock:
        _events.append(event)
        if len(_events) > MAX_EVENTS:
            del _events[:len(_events) - MAX_EVENTS]
        snapshot = list(_events)
    log.info("[%s] %s", event["kind"], event["text"])
    _save(snapshot)
    return event


def recent(limit: int = 5) -> list:
    """Последние события, самые свежие первыми."""
    with _lock:
        return list(reversed(_events[-max(1, int(limit)):]))


def describe(limit: int = 3) -> str:
    """Готовая строка для окна. Пустая — рассказывать не о чем."""
    items = recent(limit)
    if not items:
        return ""
    return "\n".join(f"{e['at']} · {e['kind']}: {e['text']}" for e in items)


def clear() -> None:
    with _lock:
        _events.clear()
    _save([])


def _save(snapshot: list) -> None:
    """Файл рядом с программой: события должны пережить и закрытие окна, и
    аварийное завершение — иначе разбирать «оно остановилось ночью» снова
    будет не по чему."""
    try:
        with open(path(), "w", encoding="utf-8") as f:
            json.dump({"events": snapshot}, f, ensure_ascii=False, indent=1)
    except OSError as e:
        log.debug("Не удалось сохранить ленту событий: %s", e)


def load() -> list:
    """Прочитать события прошлого запуска. Ошибка чтения — не беда."""
    global _events
    try:
        with open(path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("events", [])
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(items, list):
        return []
    clean = [e for e in items if isinstance(e, dict) and e.get("text")]
    with _lock:
        _events = clean[-MAX_EVENTS:]
        return list(_events)
