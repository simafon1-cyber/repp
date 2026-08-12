"""symbol_cache.py — замеры пар хранятся в файле, а не делаются каждый раз.

ЗАЧЕМ
Владелец: «пусть просто один раз загружает все пары и хранит у себя в файлах,
чтобы не было такой долгой загрузки».

Он прав, и вот почему это правильно именно так.

Замер пары стоит дорого: чтобы получить бары, пару надо добавить в «Обзор
рынка», после чего терминал идёт за историей на сервер брокера. Это и есть та
работа, из-за которой запуск выглядел зависанием. Но замеряется при этом то,
что за сутки почти не меняется: минимальный лот, цена пункта, обычный размах
бара, обычный спред. Делать эту работу при КАЖДОМ запуске незачем.

=====================================================================
КАК ЭТО РАБОТАЕТ
=====================================================================
1. Замерили пару — записали в файл symbols_survey.json рядом с программой.
2. Следующий запуск читает файл и не трогает терминал вовсе. Запуск мгновенный.
3. Пары, до которых в прошлый раз не дошла очередь, дозамеряются — по
   AUTO_PICK_SURVEY_LIMIT штук за запуск. За несколько запусков покрывается
   ВЕСЬ список брокера, и ни один из них не был долгим.
4. Самые старые записи обновляются понемногу, чтобы список не закостенел.

=====================================================================
ЧЕГО ЗДЕСЬ ОСТЕРЕГАЕМСЯ — ЧЕСТНО
=====================================================================
Спред и размах бара зависят от времени суток. Замер, сделанный ночью, покажет
широкий спред, и днём эта пара будет выглядеть хуже, чем она есть. Полностью
это не лечится — лечится только тем, что записи постепенно обновляются
(REFRESH_AFTER_HOURS) и старый ночной замер со временем вытесняется.

Поэтому же в файле хранится имя сервера брокера: у другого брокера и спреды
другие, и суффиксы имён другие. Сменился сервер — старые замеры выбрасываются
целиком, а не подмешиваются к новым.

Что НЕ хранится: счёт, деньги, пароли. Только описание инструментов, которое
и так открыто всем клиентам брокера.
"""

import json
import logging
import os
import sys
import time

log = logging.getLogger("symbol_cache")

CACHE_FILE = "symbols_survey.json"

# Через сколько часов запись считается устаревшей и просится на повторный
# замер. Сутки: за меньшее время спред и размах бара заметно не меняются, за
# большее — начинает мешать разница между ночным и дневным замером.
REFRESH_AFTER_HOURS = 24

# Формат файла. Если однажды состав полей поменяется, старый файл надо
# выбросить, а не читать наполовину.
VERSION = 1


def store_path(folder: str = "") -> str:
    """Где лежит файл. Рядом с программой — как и норма спреда."""
    if folder:
        return os.path.join(folder, CACHE_FILE)
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, CACHE_FILE)


def load(path: str = "", server: str = "") -> dict:
    """Прочитать замеры. Возвращает {symbol: строка замера}.

    Пустой ответ означает «начинаем с нуля» — и это не ошибка: файла может не
    быть, он может быть от другого брокера или от прежней версии формата.
    Любой сомнительный случай трактуем в пользу пересчёта: лишний замер стоит
    секунд, а неверные данные — денег."""
    try:
        with open(path or store_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("version") != VERSION:
        return {}
    if server and data.get("server") and data.get("server") != server:
        log.info("Замеры пар в файле от другого брокера (%s) — замерю заново",
                 data.get("server"))
        return {}
    rows = data.get("symbols")
    if not isinstance(rows, dict):
        return {}
    return {name: row for name, row in rows.items() if isinstance(row, dict)}


def save(rows: dict, server: str = "", path: str = "") -> bool:
    """Записать замеры. Не вышло — не беда: в следующий раз замерим снова."""
    try:
        with open(path or store_path(), "w", encoding="utf-8") as f:
            json.dump({"version": VERSION, "server": server or "",
                       "saved_at": time.time(),
                       "symbols": rows or {}}, f)
        return True
    except OSError as e:
        log.debug("Не удалось сохранить замеры пар: %s", e)
        return False


def is_fresh(row: dict, now: float = None,
             max_age_hours: float = REFRESH_AFTER_HOURS) -> bool:
    """Замер ещё годится? Без отметки времени — считаем негодным."""
    if not isinstance(row, dict):
        return False
    try:
        made = float(row.get("measured_at", 0) or 0)
        limit = float(max_age_hours)
    except (TypeError, ValueError):
        return False
    if made <= 0:
        return False
    if limit <= 0:
        return True                     # обновление отключено — годится всегда
    if now is None:
        now = time.time()
    # Отметка из будущего означает сбитые часы. Доверять ей нельзя, но и
    # выбрасывать замер каждый раз тоже незачем — считаем свежим ровно один
    # раз, а при следующем запуске он всё равно попадёт в очередь на обновление.
    return (now - made) < limit * 3600.0


def to_survey(all_names, cached: dict, limit: int, now: float = None) -> list:
    """Кого замерять В ЭТОТ РАЗ. Порядок — вот главное в этой функции.

    Сначала идут пары, которых в файле НЕТ вообще: пока пара не замерена, она
    не торгуется, и это прямая потеря возможности. Только потом — устаревшие
    записи: у них данные есть, просто несвежие.

    Так за несколько запусков покрывается весь список брокера, и ни один
    запуск не оказывается долгим."""
    names = [str(n) for n in (all_names or ())]
    cached = cached or {}
    if now is None:
        now = time.time()

    missing = [n for n in names if n not in cached]
    stale = [n for n in names
             if n in cached and not is_fresh(cached[n], now)]
    # Самые старые — первыми среди устаревших: обновляем то, что дальше всего
    # от правды.
    stale.sort(key=lambda n: float(cached[n].get("measured_at", 0) or 0))

    queue = missing + stale
    try:
        cap = int(limit)
    except (TypeError, ValueError):
        return queue
    return queue[:cap] if cap > 0 else queue


def merge(cached: dict, fresh_rows, now: float = None) -> dict:
    """Добавить свежие замеры к сохранённым. Свежий всегда важнее старого."""
    if now is None:
        now = time.time()
    result = dict(cached or {})
    for row in fresh_rows or ():
        if not isinstance(row, dict):
            continue
        name = row.get("symbol")
        if not name:
            continue
        stored = dict(row)
        stored["measured_at"] = now
        result[str(name)] = stored
    return result


def usable_rows(cached: dict, all_names=None) -> list:
    """Замеры, по которым можно отбирать пары прямо сейчас.

    Если передан список имён брокера — пары, которых у брокера больше нет,
    отбрасываются: инструмент могли снять с торгов, а мы бы продолжали его
    предлагать."""
    rows = []
    known = set(str(n) for n in all_names) if all_names else None
    for name, row in (cached or {}).items():
        if known is not None and name not in known:
            continue
        if isinstance(row, dict) and row.get("symbol"):
            rows.append(row)
    return rows


def describe(cached: dict, total: int, measured_now: int) -> str:
    """Одна строка человеку: сколько уже знаем и сколько осталось."""
    known = len(cached or {})
    if total <= 0:
        return "Замеров пар пока нет."
    line = f"Замерено пар: {known} из {total} у брокера"
    if measured_now:
        line += f" (в этот раз добавлено {measured_now})"
    if known < total:
        line += f". Остальные {total - known} замерю при следующих запусках"
    return line + "."
