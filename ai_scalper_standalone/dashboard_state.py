"""
dashboard_state.py — потокобезопасный "снимок" состояния для веб-дашборда.

Главный цикл (main.py) раз в итерацию кладёт сюда свежий снимок (счёт,
статус по символам, открытые позиции). Веб-дашборд (Flask, другой поток)
только ЧИТАЕТ этот снимок — никогда не обращается к MetaTrader5 напрямую.
"""

import threading

_lock = threading.Lock()
_snapshot: dict = {}


def update_snapshot(data: dict):
    with _lock:
        _snapshot.clear()
        _snapshot.update(data)


def get_snapshot() -> dict:
    with _lock:
        return dict(_snapshot)
