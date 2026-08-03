"""
config_migrate.py — дописывает в ваш config.py настройки, появившиеся
в новой версии программы.

ЗАЧЕМ
config.py — ваш личный файл, он не перезаписывается при обновлении, иначе
слетели бы ключи и настройки. Но когда в программе появляется новый параметр,
в вашем старом файле его нет. Раньше это выглядело так: на вкладке
«Настройка» поле показывалось ПУСТЫМ, а «Сохранить» ругался
«Некорректные значения в полях: PROFIT_LOCK_START_R_FRACTION, ...» —
потому что пустую строку нельзя превратить в число.

Теперь при каждом запуске программа сравнивает ваш config.py с эталоном
config.py.example и молча дописывает в конец только НЕДОСТАЮЩИЕ строки со
значениями по умолчанию.

ЧЕГО ЗДЕСЬ НЕТ
  * Ничего не перезаписывается. Если параметр уже есть — он не трогается,
    каким бы ни было значение. Ваши настройки не могут «вернуться к
    заводским».
  * Не переносятся многострочные блоки (RISK_PROFILES, MARKET_CONTEXT) и
    ничего, что не является простым значением на одной строке: их формат
    сложнее, чинить их вслепую опаснее, чем оставить как есть.
  * Не переносятся секреты (ключи, пароли, токены) — в эталоне они пустые,
    дописывать пустой ключ поверх ничего не даёт.

Запись атомарная и с проверкой синтаксиса (safe_files) — оборвавшаяся
запись не может испортить config.py.
"""

import ast
import logging
import os
import re
import sys

import safe_files

log = logging.getLogger("config_migrate")

# Эти имена не дописываем никогда: многострочные словари и служебные вещи,
# которые в config.py собираются иначе, чем в эталоне.
SKIP = {
    "RISK_PROFILES",
    "MARKET_CONTEXT",
    "RiskProfile",
    "SECURITY_SALT",
    "DASHBOARD_PASSWORD_HASH",
}


def app_dir() -> str:
    """Папка, где лежит рабочий config.py (рядом с .exe или с исходниками)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def example_path() -> str:
    """Эталон config.py.example. В собранной программе он лежит внутри .exe,
    PyInstaller распаковывает его в sys._MEIPASS."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = os.path.join(meipass, "config.py.example")
        if os.path.exists(bundled):
            return bundled
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py.example")


def _is_simple_value(node) -> bool:
    """Значение, которое безопасно скопировать как есть: число, строка, True/
    False/None, отрицательное число, а также плоский список/кортеж/словарь из
    таких же значений."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return isinstance(node.operand, ast.Constant)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_simple_value(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return (all(k is not None and _is_simple_value(k) for k in node.keys)
                and all(_is_simple_value(v) for v in node.values))
    return False


def _top_level_assignments(text: str) -> dict:
    """{имя: исходный текст строки присваивания} для всех присваиваний на
    верхнем уровне модуля."""
    out = {}
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        log.warning("Не удалось разобрать файл настроек: %s", e)
        return out
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        segment = ast.get_source_segment(text, node)
        if segment is None:
            continue
        out[target.id] = {"source": segment, "value": node.value}
    return out


def missing_keys(current_text: str, example_text: str) -> list:
    """Какие параметры есть в эталоне, но отсутствуют у пользователя.
    Порядок — как в эталоне, чтобы дописанный кусок читался осмысленно."""
    have = set(_top_level_assignments(current_text))
    result = []
    for name, item in _top_level_assignments(example_text).items():
        if name in have or name in SKIP:
            continue
        if "\n" in item["source"]:
            continue  # многострочный литерал не переносим
        if not _is_simple_value(item["value"]):
            continue
        result.append(name)
    return result


def build_patch(current_text: str, example_text: str) -> str:
    """Текст, который нужно дописать в конец config.py. Пустая строка —
    дописывать нечего."""
    names = missing_keys(current_text, example_text)
    if not names:
        return ""
    example_items = _top_level_assignments(example_text)
    lines = [
        "",
        "# --- Добавлено автоматически при обновлении программы ---",
        "# Эти настройки появились в новой версии; здесь стоят значения по",
        "# умолчанию. Менять их можно на вкладке «Настройка».",
    ]
    lines.extend(example_items[name]["source"] for name in names)
    return "\n".join(lines) + "\n"


def _replace_or_append(text: str, name: str, value_literal: str) -> str:
    """Заменить строку `name = ...` на новое значение. Если такой строки нет —
    дописать в конец. Дубликат оставлять нельзя: два присваивания одного имени
    работали бы (побеждает последнее), но человек, открывший файл, увидел бы
    два разных значения и не понял, какое действует."""
    pattern = re.compile(rf"^{re.escape(name)}\s*=.*$", re.MULTILINE)
    new_line = f"{name} = {value_literal}"
    if pattern.search(text):
        return pattern.sub(new_line, text, count=1)
    return text.rstrip("\n") + f"\n{new_line}\n"


# Одноразовые изменения уже существующих настроек.
#
# Обычно config_migrate НИЧЕГО не перезаписывает — только дописывает
# недостающее. Здесь исключение: владелец программы прямо попросил убрать
# дневной порог убытка ("удалить порог убытка"), а он лежит в файле, который
# обновление не трогает. Каждое такое изменение применяется РОВНО ОДИН РАЗ:
# в config.py остаётся отметка-ключ, и если человек потом включит настройку
# обратно, миграция её больше не тронет.
#
# Формат: (ключ-отметка, {имя настройки: новое значение}, пояснение).
ONE_TIME = [
    (
        "MIGRATED_DAILY_LOSS_LIMIT_OFF",
        {"USE_DAILY_LOSS_LIMIT": False},
        "дневной порог убытка снят: бот работает всё торговое время",
    ),
    (
        "MIGRATED_NO_TRADING_HALT",
        {
            "USE_MAX_DRAWDOWN_LIMIT": False,
            "PAUSE_MINUTES_AFTER_LOSS_STREAK": 0,
        },
        "торговля больше не останавливается: сняты лимит просадки и пауза "
        "после серии убытков. Вместо остановки ограничивается размер каждого "
        "убытка (стоп-лосс, риск на сделку, лимит совокупного риска) и "
        "снижается объём по мере серии",
    ),
]


def apply_one_time(config_path: str = "") -> list:
    """Применить одноразовые изменения из ONE_TIME. Возвращает пояснения к тем,
    что реально применились."""
    config_path = config_path or os.path.join(app_dir(), "config.py")
    if not os.path.exists(config_path):
        return []

    with open(config_path, "r", encoding="utf-8") as f:
        text = f.read()

    existing = _top_level_assignments(text)
    applied = []
    for marker, changes, note in ONE_TIME:
        if marker in existing:
            continue   # уже применяли
        for name, value in changes.items():
            text = _replace_or_append(text, name, repr(value))
        text = text.rstrip("\n") + f"\n\n# {note}\n{marker} = True\n"
        applied.append(note)

    if not applied:
        return []

    safe_files.atomic_write_text(config_path, text,
                                 validate=safe_files.validate_python_syntax)
    try:
        safe_files.restrict_to_current_user(config_path)
    except Exception:
        pass
    return applied


def sync(config_path: str = "", example: str = "") -> list:
    """Дописать недостающие настройки в config.py.

    Возвращает список добавленных имён (пустой — всё было на месте)."""
    config_path = config_path or os.path.join(app_dir(), "config.py")
    example = example or example_path()
    if not os.path.exists(config_path) or not os.path.exists(example):
        return []

    with open(config_path, "r", encoding="utf-8") as f:
        current_text = f.read()
    with open(example, "r", encoding="utf-8") as f:
        example_text = f.read()

    names = missing_keys(current_text, example_text)
    if not names:
        return []

    patch = build_patch(current_text, example_text)
    new_text = current_text.rstrip("\n") + "\n" + patch
    safe_files.atomic_write_text(config_path, new_text,
                                 validate=safe_files.validate_python_syntax)
    try:
        safe_files.restrict_to_current_user(config_path)
    except Exception:
        pass
    log.info("В config.py добавлены новые настройки: %s", ", ".join(names))
    return names
