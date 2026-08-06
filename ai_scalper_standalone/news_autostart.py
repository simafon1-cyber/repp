"""news_autostart.py — программа сама налаживает источник новостей.

ЗАЧЕМ
Новостной режим (TradingMode.NEWS_TRADING) работает только если есть
календарь. Календарь берётся из сервиса CalendarExport внутри MetaTrader:
python-библиотека MT5 календарь не отдаёт, эти функции есть только в MQL5.

Раньше цепочка была ручной и молчаливой: поставить сервис, собрать его,
запустить в Навигаторе. Если что-то из этого не сделано, бот в новостном
режиме просто не открывал сделок и писал «свежего пробоя нет» — а почему
пробоя нет, из этой фразы понять было нельзя. Настроено всё или нет,
владелец узнать не мог.

ЧТО ЗДЕСЬ ДЕЛАЕТСЯ САМО
  1. Ищутся установленные терминалы.
  2. Проверяется, лежит ли файл сервиса в MQL5/Services и собран ли он
     (рядом должен быть .ex5).
  3. Чего нет — ставится и собирается автоматически (mt5_install).
  4. Проверяется файл календаря: есть ли он и насколько свежий.
  5. Итог отдаётся человеческим текстом: что работает, что нет и что
     делать дальше.

ЧЕГО ЗДЕСЬ НЕТ, И ПОЧЕМУ
Первый запуск сервиса. У MetaTrader нет способа запустить сервис снаружи —
ни в python-библиотеке, ни через командную строку терминала. Это делается
один раз мышкой: Навигатор -> Сервисы -> CalendarExport -> Запустить.
Дальше терминал запоминает и поднимает сервис сам при каждом старте.
Обещать «запустится само» там, где это технически невозможно, нельзя:
человек будет ждать сделок, которых не будет.
"""

import logging
import os
import time

import config as cfg

log = logging.getLogger("news_autostart")

SERVICE_NAME = "CalendarExport"
SERVICE_SOURCE = SERVICE_NAME + ".mq5"
SERVICE_COMPILED = SERVICE_NAME + ".ex5"

# Как часто перепроверять цепочку в работающей программе. Проверка дешёвая
# (несколько обращений к файловой системе), но каждую секунду её гонять
# незачем: состояние меняется, когда человек что-то нажал в терминале.
RECHECK_SECONDS = 300


def _news_mode_on() -> bool:
    """Новостной режим включён (сам по себе или вместе со скальпингом)."""
    mode = getattr(cfg, "TRADING_MODE", None)
    name = getattr(mode, "name", str(mode)).upper()
    return "NEWS" in name or "BOTH" in name


def service_state(terminal_dir: str) -> dict:
    """Состояние сервиса в ОДНОМ терминале."""
    services = os.path.join(terminal_dir, "MQL5", "Services")
    source = os.path.join(services, SERVICE_SOURCE)
    compiled = os.path.join(services, SERVICE_COMPILED)
    return {
        "terminal": terminal_dir,
        "installed": os.path.exists(source),
        "compiled": os.path.exists(compiled),
    }


def calendar_state() -> dict:
    """Состояние файла календаря: пишется он сейчас или нет.

    Свежий файл — единственное надёжное доказательство, что сервис реально
    ЗАПУЩЕН. Наличие .ex5 говорит лишь о том, что он установлен и собран."""
    state = {"path": "", "exists": False, "age_seconds": None,
             "fresh": False, "error": ""}
    try:
        import news_providers as npv
        path = npv.mt5_calendar_path()
    except Exception as e:  # noqa: BLE001
        state["error"] = str(e)
        return state

    state["path"] = path
    if not os.path.exists(path):
        return state
    state["exists"] = True
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError as e:
        state["error"] = str(e)
        return state
    state["age_seconds"] = age
    try:
        import news_providers as npv
        limit = npv.MT5_CALENDAR_MAX_AGE_SECONDS
    except Exception:  # noqa: BLE001
        limit = 3600
    state["fresh"] = age <= limit
    return state


def check() -> dict:
    """Полная проверка цепочки новостей. Ничего не меняет."""
    import mt5_install

    result = {
        "news_mode": _news_mode_on(),
        "terminals": [],
        "calendar": calendar_state(),
        "needs_install": False,
        "needs_manual_start": False,
        "ready": False,
    }
    try:
        terminals = mt5_install.find_terminals()
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось найти терминалы: %s", e)
        terminals = []
    result["terminals"] = [service_state(t) for t in terminals]

    # Ставить нужно, если хотя бы в одном терминале сервиса нет или он не
    # собран. Терминалов может быть несколько (у каждого счёта свой), и
    # календарь читается из того, к которому подключена программа.
    result["needs_install"] = any(
        not s["installed"] or not s["compiled"] for s in result["terminals"])
    result["ready"] = bool(result["calendar"].get("fresh"))
    # Сервис на месте и собран, а файла нет или он протух — значит его просто
    # не запустили. Это единственный шаг, который делается руками.
    result["needs_manual_start"] = (
        bool(result["terminals"])
        and not result["needs_install"]
        and not result["ready"])
    return result


def repair(progress=None) -> list:
    """Починить то, что чинится само. Возвращает список сделанного."""
    def say(text):
        if progress:
            try:
                progress(text)
            except Exception:  # noqa: BLE001
                pass

    import mt5_install

    done = []
    state = check()
    if not state["terminals"]:
        return done
    if not state["needs_install"]:
        return done

    say("Ставлю сервис календаря в MetaTrader...")
    report = mt5_install.install_all(progress=progress)
    if report.get("copied"):
        done.append(f"сервис календаря скопирован в терминалы: {report['copied']} файлов")
    if report.get("compiled"):
        done.append(f"собрано в терминале: {report['compiled']}")
    for problem in report.get("errors", []):
        log.warning("Установка календаря: %s", problem)
    return done


def describe(state: dict) -> str:
    """Состояние цепочки человеческим языком — то, что видит владелец."""
    if not state["terminals"]:
        return ("MetaTrader 5 на этом компьютере не найден — источник новостей "
                "взять неоткуда.")

    calendar = state["calendar"]
    if state["ready"]:
        age = int((calendar.get("age_seconds") or 0) / 60)
        return (f"Календарь работает: файл обновлялся {age} мин назад. "
                f"Новостной режим готов.")

    if state["needs_install"]:
        return ("Сервис календаря ещё не установлен в терминал. Программа "
                "поставит и соберёт его сама — нажмите «Проверить и починить».")

    if not calendar.get("exists"):
        return ("Сервис календаря установлен и собран, но ни разу не "
                "запускался — файла календаря нет. Запустите его ОДИН раз: "
                "в MetaTrader откройте Навигатор (Ctrl+N) -> Сервисы -> "
                f"{SERVICE_NAME} -> правой кнопкой -> Запустить. Дальше "
                "терминал будет поднимать его сам при каждом старте.")

    age = int((calendar.get("age_seconds") or 0) / 60)
    return (f"Календарь не обновлялся {age} мин — сервис остановлен или "
            f"терминал закрыт. В MetaTrader: Навигатор -> Сервисы -> "
            f"{SERVICE_NAME} -> Запустить.")


_last_check = {"at": 0.0}


def ensure_ready(progress=None, force: bool = False) -> dict:
    """Вызывается программой сама: проверить и, если можно, починить.

    Возвращает состояние ПОСЛЕ починки. Чинит только когда включён новостной
    режим: лезть в чужой терминал, когда новости не нужны, — не наше дело."""
    now = time.time()
    if not force and now - _last_check["at"] < RECHECK_SECONDS:
        return check()
    _last_check["at"] = now

    state = check()
    if not state["news_mode"]:
        return state
    if state["needs_install"]:
        for line in repair(progress=progress):
            log.info("Календарь: %s", line)
        state = check()
    if not state["ready"]:
        log.warning("Источник новостей не готов: %s", describe(state))
    return state


def reset_checks() -> None:
    """Забыть, когда проверяли в прошлый раз (для кнопки в интерфейсе)."""
    _last_check["at"] = 0.0
