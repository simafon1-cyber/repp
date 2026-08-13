"""single_instance.py — вторая копия программы не запускается.

ЗАЧЕМ
Владелец: «при запуске программы включается две».

Две копии — это не просто неудобно. Обе подключаются к одному терминалу, обе
ведут одни и те же открытые позиции и обе двигают стоп-лосс. Каждая при этом
считает, что она одна. Итог непредсказуем: от двойных сделок до стопа,
переставленного дважды подряд в разные стороны.

=====================================================================
КАК ЭТО РАБОТАЕТ
=====================================================================
Рядом с программой лежит файл-замок с номером процесса. При запуске:

  1. Файла нет — мы первые, пишем свой номер, работаем.
  2. Файл есть и процесс с таким номером ЖИВ — значит копия уже работает,
     новая закрывается.
  3. Файл есть, а процесса нет — программа в прошлый раз завершилась
     аварийно. Замок считается брошенным, забираем его себе.

Третий случай важнее, чем кажется. Замок, который не умеет отпускаться сам,
однажды запрёт человека снаружи от его же программы — и он не будет знать,
что делать. Поэтому «жив ли процесс» проверяется всегда, а не только при
аккуратном выходе.
"""

import logging
import os
import sys

log = logging.getLogger("single_instance")

LOCK_FILE = "running.lock"


def lock_path(folder: str = "") -> str:
    if folder:
        return os.path.join(folder, LOCK_FILE)
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, LOCK_FILE)


def process_alive(pid: int) -> bool:
    """Работает ли процесс с таким номером.

    Ноль и отрицательные отсекаем отдельно: os.kill(0, 0) на POSIX означает
    «послать сигнал всей группе процессов», а не проверку, и ответил бы «жив»
    на пустом месте."""
    try:
        number = int(pid)
    except (TypeError, ValueError):
        return False
    if number <= 0:
        return False
    if number == os.getpid():
        return True
    try:
        os.kill(number, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Процесс есть, но он чужой. Для нас это «занято» — и это правильнее,
        # чем занять замок и работать вдвоём.
        return True
    except OSError:
        return False
    return True


def read_owner(path: str = "") -> int:
    """Чей сейчас замок. 0 — ничей."""
    try:
        with open(path or lock_path(), "r", encoding="utf-8") as f:
            return int((f.read() or "0").strip() or 0)
    except (OSError, ValueError):
        return 0


def acquire(path: str = "", alive=None) -> bool:
    """Занять замок. False — программа уже запущена.

    alive передаётся только тестами: подменять способ проверки «жив ли
    процесс» в бою незачем, а проверить оба исхода надо."""
    target = path or lock_path()
    check = alive or process_alive

    owner = read_owner(target)
    if owner and owner != os.getpid() and check(owner):
        log.warning("Программа уже запущена (процесс %s) — вторая копия не нужна",
                    owner)
        return False

    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError as e:
        # Записать замок не вышло — это не повод не работать. Лучше запустить
        # программу без защиты, чем не запустить вовсе: человеку нужна
        # торговля, а не наши файлы.
        log.warning("Не удалось записать файл-замок (%s) — работаю без него", e)
    return True


def release(path: str = "") -> None:
    """Отпустить замок. Чужой не трогаем: его хозяин ещё работает."""
    target = path or lock_path()
    if read_owner(target) != os.getpid():
        return
    try:
        os.remove(target)
    except OSError:
        pass
