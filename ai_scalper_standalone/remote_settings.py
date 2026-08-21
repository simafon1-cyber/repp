"""remote_settings.py — настройки торговли приходят из GitHub сами.

ЗАЧЕМ
Владелец: «сделай, чтобы он сам загружался на GitHub, чтобы ты мог проверить
в любой момент и изменить настройки торговли, и настройки сами загрузились
без всяких нажатий».

Программа раз в несколько минут читает файл `remote/settings.json` из
репозитория и применяет то, что там написано. Репозиторий публичный, поэтому
для ЧТЕНИЯ токен не нужен вовсе. Записать туда может только владелец
репозитория.

Дальше настройки подхватываются сами: main.py следит за временем изменения
config.py и перечитывает его на ходу (reload_config_if_changed) — перезапуск
не нужен.

=====================================================================
ГЛАВНОЕ: ЧЕГО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ
=====================================================================
Файл из интернета управляет реальной торговлей. Поэтому здесь ЖЁСТКИЕ рамки,
и они важнее всего остального кода в этом файле.

1. НИКАКОГО ВЫПОЛНЕНИЯ КОДА. Только JSON, только простые значения. Ни eval,
   ни exec, ни импорта — файл не может принести с собой поведение.

2. ТОЛЬКО РАЗРЕШЁННЫЕ НАСТРОЙКИ (ALLOWED ниже). Всё остальное отбрасывается
   с объяснением. Список не «на всякий случай», а по смыслу: туда входят
   ручки торговли и не входит ничего, что решает, КОМУ доверять.

3. ОСОБО ЗАПРЕЩЕНО (FORBIDDEN) — и это не формальность:
   * UPDATE_REPO / UPDATE_BRANCH / UPDATE_ENABLED. Если бы их можно было
     менять отсюда, тот, кто получил доступ к репозиторию, перенаправил бы
     обновление на свой код — и получил бы не настройку, а всю машину.
     Источник обновлений задаётся ТОЛЬКО руками на компьютере владельца.
   * токены, пароли, логины, ключи, файл сессии Telegram;
   * любые пути к файлам и папкам;
   * REQUIRE_LOGIN — снятие защиты входа.

4. LIVE_TRADING — ассиметрично: ВЫКЛЮЧИТЬ торговлю удалённо можно, ВКЛЮЧИТЬ
   нельзя. Остановка — это защита, и она должна быть доступна быстро. Запуск
   реальной торговли — решение, которое человек принимает сам, у своего
   компьютера.

5. КАЖДОЕ ЗНАЧЕНИЕ ПРОВЕРЯЕТСЯ ПО ДИАПАЗОНУ. Опечатка «риск 50%» вместо
   «0.5%» не должна доехать до счёта.

Что применилось и что отброшено — видно в ленте происшествий и в журнале.
"""

import json
import logging
import os
import urllib.error
import urllib.request

import config as cfg
import config_migrate
import runtime_events

log = logging.getLogger("remote_settings")

MARKER = "REMOTE_SETTINGS_APPLIED"   # id последней применённой правки
FILE_IN_REPO = "remote/settings.json"


# =====================================================================
# ЧТО МОЖНО МЕНЯТЬ
# =====================================================================
def _num(low, high):
    def rule(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, "нужно число"
        if not (low <= value <= high):
            return None, f"допустимо от {low} до {high}"
        return float(value), ""
    return rule


def _whole(low, high):
    def rule(value):
        if isinstance(value, bool) or not isinstance(value, int):
            return None, "нужно целое число"
        if not (low <= value <= high):
            return None, f"допустимо от {low} до {high}"
        return int(value), ""
    return rule


def _flag(value):
    if not isinstance(value, bool):
        return None, "нужно true или false"
    return value, ""


def _symbols(value):
    if not isinstance(value, list) or len(value) > 40:
        return None, "нужен список имён, не длиннее 40"
    clean = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None, "в списке должны быть только имена пар"
        name = item.strip().upper()
        if len(name) > 20 or not all(c.isalnum() or c in "._-/" for c in name):
            return None, f"недопустимое имя пары: {item!r}"
        clean.append(name)
    return clean, ""


def _ladder(value):
    """Лестница трейлинга: список пар (порог в R, сколько запереть в R)."""
    if not isinstance(value, list) or not (1 <= len(value) <= 12):
        return None, "нужен список из 1-12 ступеней"
    steps = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None, "каждая ступень — это пара чисел [порог, сколько запереть]"
        trigger, lock = item
        for x in (trigger, lock):
            if isinstance(x, bool) or not isinstance(x, (int, float)):
                return None, "ступени задаются числами"
        if not (0 < trigger <= 20) or not (0 <= lock <= 20):
            return None, "порог от 0 до 20 R, фиксация от 0 до 20 R"
        if lock >= trigger:
            # Иначе стоп оказался бы ВПЕРЕДИ цены и сработал бы мгновенно.
            return None, f"ступень {trigger}: запирать {lock} нельзя, это не меньше порога"
        steps.append((float(trigger), float(lock)))
    return steps, ""


def _choice(*options):
    def rule(value):
        if not isinstance(value, str) or value not in options:
            return None, "допустимо: " + ", ".join(options)
        return value, ""
    return rule


# Ручки торговли — и ничего, что решает, кому доверять.
ALLOWED = {
    # трейлинг и фиксация прибыли
    "USE_R_TRAIL_LADDER": _flag,
    "R_TRAIL_LADDER": _ladder,
    "R_TRAIL_GIVEBACK_R": _num(0, 5),
    "TP_TIGHTEN_SHRINK_PER_MINUTE": _num(0, 1),
    "TP_TIGHTEN_MIN_FRACTION": _num(0.01, 1),
    "TP_TIGHTEN_MIN_R": _num(0.5, 5),
    "USE_PARTIAL_CLOSE": _flag,
    "PARTIAL_CLOSE_PERCENT": _num(10, 90),

    # риск
    "MAX_TRADE_RISK_PERCENT_OF_EQUITY": _num(0, 10),
    "MAX_POSITION_LEVERAGE": _num(0, 500),
    "MAX_SIMULTANEOUS_POSITIONS": _whole(0, 50),
    "MIN_SL_SPREAD_MULTIPLE": _num(0, 50),
    "MIN_SL_ATR_FRACTION": _num(0, 10),

    # инструменты
    "SYMBOLS": _symbols,
    "BLOCKED_SYMBOLS": _symbols,

    # состояние рынка
    "USE_MARKET_CLOSED_GUARD": _flag,
    "MARKET_DEAD_SECONDS": _num(0, 3600),
    "USE_THIN_MARKET_GUARD": _flag,
    "THIN_SPREAD_RATIO": _num(0, 50),
    "THIN_MIN_SAMPLES": _whole(1, 1000),

    # новости
    "NEWS_TRADE_MIN_IMPACT": _choice("low", "medium", "high"),

    # аварийная остановка (см. проверку ниже — включить обратно нельзя)
    "LIVE_TRADING": _flag,
}

# Не просто «нет в ALLOWED», а названо явно: так понятнее и людям, и тесту.
FORBIDDEN = {
    "UPDATE_REPO", "UPDATE_BRANCH", "UPDATE_ENABLED", "UPDATE_TOKEN",
    "JOURNAL_TOKEN", "JOURNAL_REPO", "JOURNAL_BRANCH",
    "DASHBOARD_LOGIN", "DASHBOARD_PASSWORD", "DASHBOARD_PASSWORD_HASH",
    "SECURITY_SALT", "REQUIRE_LOGIN",
    "MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "MT5_PATH",
    "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION_PATH",
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FINNHUB_API_KEY",
    "LOG_CSV_PATH", "LOG_FILE_PATH",
    # Предторговый барьер. Менять его удалённо нельзя ни в какую сторону:
    # тот, кто может выключить барьер или подставить другой номер счёта,
    # может увести заявки на чужой счёт — а это ровно то, от чего барьер
    # и поставлен. Задаётся только руками, в config.py, на своём компьютере.
    "DEMO_ACCEPTANCE_MODE", "DEMO_ACCEPTANCE_LOGIN",
    "DEMO_ACCEPTANCE_SERVER", "DEMO_ACCEPTANCE_REQUIRE_DEMO",
}


# =====================================================================
# ПРОВЕРКА
# =====================================================================
def validate(settings: dict) -> tuple:
    """(что применить, [почему отброшено]). Ничего не пишет и не качает."""
    accepted, rejected = {}, []
    if not isinstance(settings, dict):
        return {}, ["настройки должны быть объектом вида {имя: значение}"]

    for name, value in settings.items():
        if name in FORBIDDEN:
            rejected.append(f"{name}: менять удалённо запрещено")
            continue
        rule = ALLOWED.get(name)
        if rule is None:
            rejected.append(f"{name}: неизвестная или неразрешённая настройка")
            continue
        clean, problem = rule(value)
        if problem:
            rejected.append(f"{name}: {problem}")
            continue
        # Остановить торговлю удалённо можно, запустить — нет. Остановка это
        # защита; запуск реальной торговли человек включает сам, у своего
        # компьютера.
        if name == "LIVE_TRADING" and clean is True:
            rejected.append("LIVE_TRADING: включить торговлю удалённо нельзя, "
                            "только выключить")
            continue
        accepted[name] = clean
    return accepted, rejected


# =====================================================================
# ЗАГРУЗКА
# =====================================================================
def settings_url() -> str:
    """Прямая ссылка на файл настроек в репозитории. Токен не нужен:
    репозиторий публичный, а читаем мы только один текстовый файл."""
    custom = str(getattr(cfg, "REMOTE_SETTINGS_URL", "") or "").strip()
    if custom:
        return custom
    repo = str(getattr(cfg, "UPDATE_REPO", "") or "").strip()
    if not repo:
        return ""
    branch = str(getattr(cfg, "UPDATE_BRANCH", "") or "").strip() or "main"
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{FILE_IN_REPO}"


def fetch(url: str = "", timeout: float = 20.0):
    """(данные, текст ошибки). Файла нет — это не ошибка: значит менять
    нечего, и молчать здесь правильно."""
    url = url or settings_url()
    if not url:
        return None, "не задан репозиторий обновлений"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read(200_000)      # больше файлу настроек не нужно
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, ""                   # файла просто нет
        return None, f"GitHub ответил {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, f"нет связи с GitHub: {e}"
    try:
        return json.loads(raw.decode("utf-8")), ""
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return None, f"файл настроек испорчен: {e}"


# =====================================================================
# ПРИМЕНЕНИЕ
# =====================================================================
def config_path() -> str:
    return os.path.join(config_migrate.app_dir(), "config.py")


def already_applied(change_id: str, path: str = "") -> bool:
    """Эту правку уже применяли? Отметка лежит в самом config.py."""
    path = path or config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    marks = config_migrate._top_level_assignments(text).get(MARKER)
    if marks is None:
        return False
    try:
        import ast
        return ast.literal_eval(marks["value"]) == change_id
    except (ValueError, SyntaxError, AttributeError):
        return False


def apply(data: dict, path: str = "") -> dict:
    """Применить прочитанное. Возвращает отчёт для человека."""
    path = path or config_path()
    report = {"applied": {}, "rejected": [], "note": "", "changed": False}

    if not isinstance(data, dict):
        report["note"] = "файл настроек должен быть объектом"
        return report

    change_id = str(data.get("id", "")).strip()
    if not change_id:
        report["note"] = "у правки нет поля id — не могу отличить новую от старой"
        return report
    if already_applied(change_id, path):
        report["note"] = f"правка {change_id} уже применена"
        return report

    accepted, rejected = validate(data.get("settings", {}))
    report["rejected"] = rejected
    if not accepted:
        report["note"] = "применять нечего"
        # Отметку всё равно ставим: иначе программа будет пытаться применить
        # эту же негодную правку каждые несколько минут и каждый раз ругаться.
        _write(path, {}, change_id)
        return report

    if not _write(path, accepted, change_id):
        report["note"] = "не удалось записать config.py"
        return report

    report["applied"] = accepted
    report["changed"] = True
    return report


def _write(path: str, settings: dict, change_id: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        log.warning("Не удалось прочитать config.py: %s", e)
        return False

    for name, value in settings.items():
        text = config_migrate._replace_or_append(text, name, repr(value))
    text = config_migrate._replace_or_append(text, MARKER, repr(change_id))

    try:
        # Через safe_files: запись атомарная и с проверкой синтаксиса, то есть
        # оборвавшаяся запись не может оставить человека без настроек.
        import safe_files
        safe_files.atomic_write_text(path, text,
                                     validate=safe_files.validate_python_syntax)
        return True
    except Exception as e:            # noqa: BLE001 — причина уходит в отчёт
        log.warning("Не удалось записать настройки из GitHub: %s", e)
        return False


def sync() -> dict:
    """Полный цикл: скачать, проверить, применить, рассказать."""
    if not getattr(cfg, "REMOTE_SETTINGS_ENABLED", False):
        return {"applied": {}, "rejected": [], "note": "выключено", "changed": False}

    data, problem = fetch()
    if problem:
        return {"applied": {}, "rejected": [], "note": problem, "changed": False}
    if data is None:
        return {"applied": {}, "rejected": [], "note": "", "changed": False}

    report = apply(data)

    if report["changed"]:
        names = ", ".join(sorted(report["applied"]))
        runtime_events.record("настройки", f"из GitHub применено: {names}")
        log.info("Настройки из GitHub применены: %s", report["applied"])
    for reason in report["rejected"]:
        # Отказ — это не мелочь: человек ждал изменения, а его не будет.
        runtime_events.record("настройки", f"из GitHub отклонено — {reason}")
        log.warning("Настройка из GitHub отклонена: %s", reason)
    return report


def describe(report: dict) -> str:
    """Одна строка для окна."""
    if not report:
        return ""
    if report.get("changed"):
        return "Применены настройки из GitHub: " + ", ".join(sorted(report["applied"]))
    if report.get("rejected"):
        return "Настройки из GitHub отклонены: " + "; ".join(report["rejected"])
    return report.get("note", "")
