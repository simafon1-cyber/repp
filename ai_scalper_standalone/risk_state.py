"""risk_state.py — защита счёта переживает перезапуск программы.

=====================================================================
ЗАЧЕМ ЭТОТ ФАЙЛ ПОЯВИЛСЯ
=====================================================================
Лимит просадки — последний рубеж защиты денег. Считается он от ПИКА счёта:
сколько процентов счёт потерял от своего максимума. Пик хранился в
AccountState.peak_equity, то есть в памяти процесса.

Отсюда дыра, которую видел владелец и описывал как «работает пару часов и
всё, потом надо перезапуск»: счёт просел от максимума, вход закрылся — а
перезапуск обнулял пик до текущего эквити, просадка становилась нулевой, и
торговля возобновлялась как ни в чём не бывало. Защита, которая снимается
перезапуском, защитой не является.

То же самое касается дневного лимита убытка: он считается от эквити на
начало дня, и перезапуск в середине дня подменял «начало дня» текущим
значением. Потеряли 4% из разрешённых 5, перезапустились — и снова можно
терять 5%, теперь уже от меньшего числа.

Здесь это состояние хранится в файле рядом с программой.

=====================================================================
ЧЕГО ЭТОТ ФАЙЛ НЕ ДЕЛАЕТ
=====================================================================
Он НЕ меняет ни одного порога и ни одного правила. Лимиты, проценты и
условия остаются ровно теми же, что были. Меняется только одно: числа, от
которых они считаются, больше не теряются при перезапуске.

Состояние хранится ОТДЕЛЬНО ПО КАЖДОМУ СЧЁТУ (ключ — номер счёта). Программа
умеет работать с несколькими счетами, и смешивать их пики нельзя: пик одного
счёта, применённый к другому, закрыл бы торговлю на ровном месте.

Любая проблема с файлом (нет, испорчен, чужой формат) ошибкой не считается:
программа начинает с чистого листа, как и раньше. Не запустить торговлю
из-за нечитаемого вспомогательного файла было бы хуже, чем не прочитать его.
"""

import json
import logging
import os
from datetime import datetime

import safe_files

log = logging.getLogger("risk_state")

STATE_FILE = "risk_state.json"
VERSION = 1

# Последнее, что записано на диск. Нужно, чтобы не писать файл на каждом
# проходе главного цикла: пик обновляется редко, а цикл крутится каждые
# несколько секунд.
_последнее: dict = {}


def store_path(folder: str = "") -> str:
    base = folder or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, STATE_FILE)


def _key(login) -> str:
    """Номер счёта строкой. Пусто — счёт неизвестен, состояние не храним."""
    try:
        value = int(login or 0)
    except (TypeError, ValueError):
        return ""
    return str(value) if value > 0 else ""


def snapshot(acc_state) -> dict:
    """Что именно сохраняется. Отдельная функция — чтобы сравнивать с уже
    записанным и не трогать диск, когда ничего не изменилось."""
    day = getattr(acc_state, "last_trade_day", None)
    return {
        "peak_equity": round(float(getattr(acc_state, "peak_equity", 0) or 0), 2),
        "day_start_equity": round(float(getattr(acc_state, "day_start_equity", 0) or 0), 2),
        "day": day.strftime("%Y-%m-%d") if isinstance(day, datetime) else "",
        "trades_today": int(getattr(acc_state, "trades_today", 0) or 0),
    }


def save(acc_state, login, path: str = "") -> bool:
    """Записать состояние счёта. True — записано."""
    key = _key(login)
    if not key:
        return False
    target = path or store_path()
    data = {"version": VERSION, "accounts": {}}
    try:
        if os.path.exists(target):
            with open(target, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded.get("accounts"), dict):
                data["accounts"] = loaded["accounts"]
    except Exception as e:  # noqa: BLE001
        log.warning("Файл %s не читается (%s) — перезапишу заново", target, e)

    data["accounts"][key] = snapshot(acc_state)
    try:
        # backup=False: файл маленький и восстановимый. В худшем случае
        # программа начнёт считать пик заново — ровно как было до этой правки.
        safe_files.atomic_write_text(target,
                                     json.dumps(data, ensure_ascii=False, indent=1),
                                     backup=False)
        _последнее[key] = data["accounts"][key]
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось сохранить состояние риска в %s: %s", target, e)
        return False


def save_if_changed(acc_state, login, path: str = "") -> bool:
    """Записать, только если что-то изменилось с прошлого раза.

    Главный цикл крутится каждые несколько секунд, а пик счёта обновляется
    редко. Писать файл на каждом проходе — изнашивать диск без всякой
    пользы."""
    key = _key(login)
    if not key:
        return False
    if _последнее.get(key) == snapshot(acc_state):
        return False
    return save(acc_state, login, path)


def load(acc_state, login, path: str = "") -> str:
    """Восстановить состояние счёта. Возвращает, что именно восстановлено.

    ПИК НИКОГДА НЕ ОПУСКАЕТСЯ. Берётся большее из сохранённого и текущего:
    если счёт вырос, пик тем более вырос; если просел — сохранённый пик и
    есть то самое число, ради которого всё это писалось.

    НАЧАЛО ДНЯ восстанавливается ТОЛЬКО за сегодняшнее число. Вчерашнее
    начало дня сегодня не значит ничего, а смену дня программа обрабатывает
    сама (check_new_day)."""
    key = _key(login)
    if not key:
        return ""
    target = path or store_path()
    if not os.path.exists(target):
        return ""
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        saved = (data.get("accounts") or {}).get(key)
    except Exception as e:  # noqa: BLE001
        log.warning("Состояние риска %s не читается (%s) — начну заново", target, e)
        return ""
    if not isinstance(saved, dict):
        return ""

    вести = []
    try:
        peak = float(saved.get("peak_equity", 0) or 0)
    except (TypeError, ValueError):
        peak = 0.0
    if peak > float(getattr(acc_state, "peak_equity", 0) or 0):
        acc_state.peak_equity = peak
        вести.append(f"пик счёта {peak:.2f}")

    сегодня = datetime.now().strftime("%Y-%m-%d")
    if str(saved.get("day", "")) == сегодня:
        try:
            start = float(saved.get("day_start_equity", 0) or 0)
        except (TypeError, ValueError):
            start = 0.0
        if start > 0:
            acc_state.day_start_equity = start
            acc_state.last_trade_day = datetime.now()
            вести.append(f"начало дня {start:.2f}")
        try:
            acc_state.trades_today = int(saved.get("trades_today", 0) or 0)
        except (TypeError, ValueError):
            pass

    _последнее[key] = snapshot(acc_state)
    return ", ".join(вести)
