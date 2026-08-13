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


# Windows: числа из его же документации, чтобы не разбрасывать «магию» по коду.
_ЕЩЁ_РАБОТАЕТ = 259          # STILL_ACTIVE — процесс не завершился
_СПРОСИТЬ_НЕМНОГО = 0x1000   # PROCESS_QUERY_LIMITED_INFORMATION


def _alive_windows(number: int) -> bool:
    """Жив ли процесс — по-виндовому, БЕЗ os.kill.

    ПОЧЕМУ ОТДЕЛЬНО ДЛЯ WINDOWS. Здесь стояла проверка os.kill(pid, 0) —
    приём, правильный на Linux и опасный на Windows. Windows не знает
    «сигнала 0»: Python выполняет os.kill через TerminateProcess, то есть
    проверка «жив ли он» УБИВАЛА БЫ найденный процесс. А когда номер
    оказывался чужим или устаревшим, вылезала ошибка [WinError 6] The handle
    is invalid, которая к тому же приходила как SystemError и мимо
    `except OSError` — программа падала на запуске, не показав окна.
    Владелец видел это как «Unhandled exception in script».

    Здесь вместо этого просто СПРАШИВАЕТСЯ состояние процесса: открываем его
    с минимальными правами (только чтение сведений) и смотрим код выхода."""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(_СПРОСИТЬ_НЕМНОГО, False, number)
    if not handle:
        # Не открылся: либо такого процесса нет, либо он чужой. В обоих
        # случаях отвечаем «свободно». Своя же копия, запущенная тем же
        # человеком, всегда открывается — а вот чужой процесс со случайно
        # совпавшим номером иначе запер бы владельца снаружи от его
        # собственной программы. Из двух бед вторая хуже.
        return False
    try:
        код = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(код)):
            return False
        return код.value == _ЕЩЁ_РАБОТАЕТ
    finally:
        kernel32.CloseHandle(handle)


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
        if os.name == "nt":
            return _alive_windows(number)
        os.kill(number, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # POSIX: процесс есть, но он чужой. Для нас это «занято» — и это
        # правильнее, чем занять замок и работать вдвоём.
        return True
    except Exception as e:  # noqa: BLE001
        # ЛОВИМ ВСЁ. Эта проверка выполняется до появления окна, и любая
        # неожиданность здесь означает не «программа занята», а «программа
        # не запустилась вовсе» — молча, одним системным окном с трассировкой.
        # Ровно так и случилось на Windows. Не поняли — считаем свободно:
        # человеку нужна торговля, а не наша аккуратность.
        log.warning("Не удалось проверить процесс %s (%s) — считаю свободным",
                    number, e)
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
    if owner and owner != os.getpid():
        try:
            занято = check(owner)
        except Exception as e:  # noqa: BLE001
            # Ни одна поломка ЗДЕСЬ не имеет права помешать запуску. Это
            # первое, что выполняется при старте, и падение тут выглядит как
            # «программа не открывается вовсе» — без окна, без объяснения.
            log.warning("Проверка запущенной копии сорвалась (%s) — запускаюсь", e)
            занято = False
        if занято:
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
