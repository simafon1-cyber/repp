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
import json
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
        "MIGRATED_TRADE_RISK_CAP",
        {"MAX_TRADE_RISK_PERCENT_OF_EQUITY": 2.0},
        "включён потолок риска на одну сделку (2% счёта): инструмент, чей "
        "минимальный лот в него не помещается, для этого депозита слишком "
        "дорог. По реальному отчёту 85% всех потерь давало одно золото, "
        "рисковавшее 6.9% счёта за сделку вместо настроенных 0.1%",
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


def _clear_stale_update_branch(text: str, existing: dict) -> tuple:
    """Одноразовая правка вне общего списка ONE_TIME, потому что она условная:
    трогать UPDATE_BRANCH можно, только если там буквально "main".

    Раньше пустое поле «Ветка» при сохранении настроек принудительно
    заменялось на "main", даже если в репозитории такой ветки никогда не
    было (обычное дело, пока не сделан ни один Pull Request) — обновление
    отвечало «Репозиторий или ветка не найдены» на каждый файл. Теперь пустая
    строка означает «программа сама узнает у GitHub главную ветку» — то же
    самое, если в репозитории main действительно существует, и рабочее вместо
    ошибки, если её там нет. Стирать безопасно ТОЛЬКО значение "main": если
    человек нарочно вписал свою ветку ("develop", ветка задачи и т.п.), это
    его осознанный выбор, и трогать его нельзя."""
    marker = "MIGRATED_UPDATE_BRANCH_AUTO"
    if marker in existing:
        return text, ""
    node = existing.get("UPDATE_BRANCH")
    if node is None:
        return text, ""
    value = node["value"]
    if not (isinstance(value, ast.Constant) and value.value == "main"):
        return text, ""
    text = _replace_or_append(text, "UPDATE_BRANCH", repr(""))
    note = ("поле «Ветка» обновления очищено: раньше пустое поле "
            "принудительно заменялось на \"main\", даже если такой ветки в "
            "репозитории нет. Теперь программа сама узнаёт главную ветку у "
            "GitHub")
    text = text.rstrip("\n") + f"\n\n# {note}\n{marker} = True\n"
    return text, note


def _line_start_offsets(text: str) -> list:
    """Абсолютное смещение начала каждой строки (lineno в ast — 1-based)."""
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _node_span(text: str, node, line_offsets: list) -> tuple:
    """(начало, конец) узла ast как абсолютные смещения в text."""
    start = line_offsets[node.lineno - 1] + node.col_offset
    end = line_offsets[node.end_lineno - 1] + node.end_col_offset
    return start, end


# Множитель ATR для стоп-лосса по профилям риска — старые (тесные) значения,
# из-за которых стоп стоял внутри обычного рыночного шума и сделки
# закрывались за 8-11 секунд, не успев никуда пойти. См. пояснение к
# _widen_stale_stop_loss() ниже.
_OLD_ATR_SL_MULTIPLIER = {
    "CONSERVATIVE": 1.0,
    "BALANCED": 1.2,
    "AGGRESSIVE": 0.8,
    "HYSTERIC": 0.5,
}
_NEW_ATR_SL_MULTIPLIER = {
    "CONSERVATIVE": 2.5,
    "BALANCED": 2.5,
    "AGGRESSIVE": 2.0,
    "HYSTERIC": 1.5,
}


def _widen_stale_stop_loss(text: str) -> tuple:
    """Одноразово раздвигает стоп-лосс в УЖЕ СУЩЕСТВУЮЩЕМ config.py.

    RISK_PROFILES — многострочный словарь, поэтому sync() его не трогает
    (см. SKIP), а обычный ONE_TIME работает только с простыми присваиваниями
    "ИМЯ = значение" на одной строке. Здесь — узкая правка ИМЕННО
    atr_sl_multiplier внутри каждого профиля, и только если он до сих пор
    равен старому заводскому значению: если человек уже подправил его вручную,
    трогать нельзя ни в коем случае.

    Правка сделана через ast с точными смещениями в исходном тексте (а не
    "собрать RISK_PROFILES заново"), чтобы ничего больше в файле — форматирование,
    остальные поля, комментарии пользователя — не изменилось ни на символ."""
    marker = "MIGRATED_WIDER_STOP_LOSS"
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text, ""
    if any(isinstance(n, ast.Assign) and len(n.targets) == 1
           and isinstance(n.targets[0], ast.Name) and n.targets[0].id == marker
           for n in tree.body):
        return text, ""

    target = None
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "RISK_PROFILES"
                and isinstance(node.value, ast.Dict)):
            target = node.value
            break
    if target is None:
        return text, ""

    line_offsets = _line_start_offsets(text)
    replacements = []   # (start, end, новый_текст) — применяются справа налево
    touched = []
    for key_node, value_node in zip(target.keys, target.values):
        if not (isinstance(key_node, ast.Attribute)
                and key_node.attr in _OLD_ATR_SL_MULTIPLIER):
            continue
        if not isinstance(value_node, ast.Call):
            continue
        for kw in value_node.keywords:
            if kw.arg != "atr_sl_multiplier":
                continue
            if not isinstance(kw.value, ast.Constant):
                continue
            old_expected = _OLD_ATR_SL_MULTIPLIER[key_node.attr]
            try:
                current = float(kw.value.value)
            except (TypeError, ValueError):
                continue
            if abs(current - old_expected) > 1e-9:
                continue   # уже другое значение — не своё дело трогать
            new_value = _NEW_ATR_SL_MULTIPLIER[key_node.attr]
            start, end = _node_span(text, kw.value, line_offsets)
            replacements.append((start, end, repr(new_value)))
            touched.append(key_node.attr)

    if not replacements:
        return text, ""

    for start, end, new_text in sorted(replacements, key=lambda r: -r[0]):
        text = text[:start] + new_text + text[end:]

    note = (f"стоп-лосс расширен в профилях риска ({', '.join(sorted(touched))}): "
           f"тесный стоп стоял внутри рыночного шума, сделки закрывались за "
           f"секунды, не успев никуда пойти")
    text = text.rstrip("\n") + f"\n\n# {note}\n{marker} = True\n"
    return text, note


# Пороги минимальной дистанции стопа — тоже старые/новые значения, простые
# top-level присваивания (в отличие от RISK_PROFILES), но правим их так же
# ОСТОРОЖНО: только если пользователь их не трогал.
_STOP_FLOOR_DEFAULTS = [
    ("MIN_SL_SPREAD_MULTIPLE", 4.0, 8.0),
    ("MIN_SL_ATR_FRACTION", 0.8, 1.5),
]


def _widen_stop_floor(text: str, existing: dict) -> tuple:
    """Раздвигает минимальную дистанцию стопа (MIN_SL_*), только если она
    всё ещё равна старому заводскому значению — та же логика бережности, что
    и у _widen_stale_stop_loss(), но для простых присваиваний."""
    marker = "MIGRATED_WIDER_STOP_FLOOR"
    if marker in existing:
        return text, ""
    touched = []
    for name, old_value, new_value in _STOP_FLOOR_DEFAULTS:
        node = existing.get(name)
        if node is None:
            continue
        value_node = node["value"]
        if not isinstance(value_node, ast.Constant):
            continue
        try:
            current = float(value_node.value)
        except (TypeError, ValueError):
            continue
        if abs(current - old_value) > 1e-9:
            continue
        text = _replace_or_append(text, name, repr(new_value))
        touched.append(name)
    if not touched:
        return text, ""
    note = ("минимальная дистанция стопа увеличена (" + ", ".join(touched) +
           "): раньше стоп мог оказаться внутри спреда и обычного шума инструмента")
    text = text.rstrip("\n") + f"\n\n# {note}\n{marker} = True\n"
    return text, note


def _default_update_repo(text: str, existing: dict) -> tuple:
    """Одноразово включает самообновление "из коробки": программа всегда
    обновляется САМА СОБОЙ из репозитория, в котором живёт её код, — вписывать
    владельца/название и включать галочку вручную не нужно.

    Условная правка (не входит в ONE_TIME): трогает UPDATE_REPO/UPDATE_ENABLED,
    только если ОБА до сих пор на старом заводском значении ("" и False) —
    это единственный надёжный признак, что настройку обновления вообще никто
    не открывал. Если человек уже вписал свой репозиторий (пусть даже с
    опечаткой) или явно включил/выключил проверку — это его осознанный выбор,
    и трогать нельзя ни в одном из полей."""
    marker = "MIGRATED_DEFAULT_UPDATE_REPO"
    if marker in existing:
        return text, ""

    def _current(name, fallback):
        node = existing.get(name)
        if node is None:
            return fallback
        value_node = node["value"]
        if isinstance(value_node, ast.Constant):
            return value_node.value
        return fallback

    repo_untouched = _current("UPDATE_REPO", "") == ""
    enabled_untouched = _current("UPDATE_ENABLED", False) is False
    if not (repo_untouched and enabled_untouched):
        return text, ""

    text = _replace_or_append(text, "UPDATE_REPO", repr("simafon1-cyber/repp"))
    text = _replace_or_append(text, "UPDATE_ENABLED", repr(True))
    note = ("самообновление включено по умолчанию: программа сама проверяет "
           "новую версию в simafon1-cyber/repp — вписывать репозиторий и "
           "включать галочку вручную больше не нужно")
    text = text.rstrip("\n") + f"\n\n# {note}\n{marker} = True\n"
    return text, note


def accounts_path() -> str:
    """Список счетов лежит РЯДОМ С ПРОГРАММОЙ, а не в папке модуля — та в
    собранном .exe временная и удаляется при выходе (см. accounts.app_dir)."""
    return os.path.join(app_dir(), "accounts.json")


def clear_account_daily_loss(path: str = "") -> str:
    """Одноразово убирает дневной порог убытка у УЖЕ СОХРАНЁННЫХ счетов.

    Этот порог (daily_loss_percent в accounts.json) — единственная дневная
    остановка, которая ещё срабатывала: общий USE_DAILY_LOSS_LIMIT давно
    выключён, но счёт хранит СВОЙ порог и глобальную галочку не читает.
    Поймав −3% за день, счёт закрывал позиции и молчал до завтра, хотя
    владелец программы просил остановки убрать.

    Правка идёт по JSON, БЕЗ расшифровки паролей: зашифрованные строки
    (password_encrypted) переписываются как есть — так пароли счетов
    физически не могут пострадать (именно этим раньше ломался save()).
    Применяется ровно один раз — в файле остаётся отметка; если человек
    потом впишет порог заново, миграция его больше не тронет."""
    path = path or accounts_path()
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return ""   # испорченный файл чинить вслепую опаснее, чем оставить
    if not isinstance(data, dict) or data.get("migrated_daily_loss_off"):
        return ""

    touched = []
    for item in data.get("accounts", []):
        if not isinstance(item, dict):
            continue
        try:
            current = float(item.get("daily_loss_percent", 0) or 0)
        except (TypeError, ValueError):
            current = 0.0
        if current > 0:
            item["daily_loss_percent"] = 0.0
            touched.append(str(item.get("name") or item.get("login") or "счёт"))

    data["migrated_daily_loss_off"] = True
    text = json.dumps(data, ensure_ascii=False, indent=2)
    safe_files.atomic_write_text(path, text)
    try:
        safe_files.restrict_to_current_user(path)
    except Exception:  # noqa: BLE001
        pass
    if not touched:
        return ""
    return ("дневной порог убытка снят у счетов (" + ", ".join(touched) +
            "): счёт больше не останавливается до завтра, поймав убыток за день")


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

    text, branch_note = _clear_stale_update_branch(text, existing)
    if branch_note:
        applied.append(branch_note)

    existing = _top_level_assignments(text)
    text, repo_note = _default_update_repo(text, existing)
    if repo_note:
        applied.append(repo_note)

    # Стоп-лосс расширяется в config.py, УЖЕ существующем у пользователя.
    # RISK_PROFILES и MIN_SL_* не входят в generic-миграцию выше (RISK_PROFILES
    # многострочный и в SKIP, а MIN_SL_* нужно трогать только если пользователь
    # их не менял) — эти два шага пересчитывают `existing`/`text` заново, потому
    # что предыдущие шаги могли уже изменить текст.
    existing = _top_level_assignments(text)
    text, stop_note = _widen_stale_stop_loss(text)
    if stop_note:
        applied.append(stop_note)
    existing = _top_level_assignments(text)
    text, floor_note = _widen_stop_floor(text, existing)
    if floor_note:
        applied.append(floor_note)

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
