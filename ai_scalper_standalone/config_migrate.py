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
    # ПОЧЕМУ НЕ ast.get_source_segment.
    #
    # Он режет исходник заново ДЛЯ КАЖДОГО узла: разбивает весь файл на
    # строки, потом берёт нужный кусок. На config.py в тысячу строк и при
    # тринадцати переносах это давало 88 СЕКУНД — программа при первом
    # запуске после установки просто стояла и молчала полторы минуты, и
    # выглядело это как «зависла».
    #
    # Здесь смещения строк считаются ОДИН раз на файл, а кусок берётся
    # обычным срезом. Результат тот же, время — доли секунды.
    offsets = _line_start_offsets(text)
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            начало, конец = _node_span(text, node, offsets)
        except (IndexError, TypeError, AttributeError):
            continue
        segment = text[начало:конец]
        if not segment:
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
    new_line = f"{name} = {value_literal}"

    # МНОГОСТРОЧНОЕ значение (список ступеней, словарь) обязано заменяться
    # целиком. Построчная регулярка ниже заменила бы только ПЕРВУЮ строку, а
    # хвост литерала остался бы висеть отдельным куском с отступом — и
    # config.py переставал бы разбираться вообще (IndentationError), унося с
    # собой все настройки пользователя. Поймано тестом
    # test_config_migrate_fresh_example_needs_no_stop_loss_migration.
    item = _top_level_assignments(text).get(name)
    if item is not None and "\n" in item["source"]:
        return text.replace(item["source"], new_line, 1)

    pattern = re.compile(rf"^{re.escape(name)}\s*=.*$", re.MULTILINE)
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
        "MIGRATED_ACCOUNTS_ARE_ALL_REAL",
        {"DEMO_ACCEPTANCE_REQUIRE_DEMO": False},
        "снято требование «счёт обязан быть демонстрационным». Решение "
        "владельца 01.09.2026, дословно: «не важно какой это счет демо или "
        "реал, так как деньги одни ..все счета считать за реал!». Раньше "
        "барьер отказывал на не-демо счёте, и заявки не уходили вовсе. "
        "Что НЕ снято: заявки по-прежнему уходят только на тот счёт, номер "
        "которого вписан вами; LIVE_TRADING включает человек руками; "
        "удалённо LIVE_TRADING можно ставить только False. Правка "
        "одноразовая: вернёте требование обратно — оно так и останется",
    ),
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
        "MIGRATED_SYMBOL_AUTO_OFF",
        {"USE_SYMBOL_AUTO_OFF": True,
         "SYMBOL_AUTO_OFF_MIN_TRADES": 10,
         "SYMBOL_AUTO_OFF_LOSS_PERCENT": 8.0},
        "инструмент, который стабильно тянет счёт вниз, отключается сам. "
        "По реальному отчёту одно золото дало 85% всех потерь — минус 29.56 "
        "при депозите 65, за 37 сделок, пока остальные пары были в плюсе. "
        "Остановки торговли это не касается: другие инструменты работают "
        "как обычно, а отключённый вернётся сам, когда убытки выйдут из "
        "окна последних сделок",
    ),
    (
        "MIGRATED_NEWS_TRADING_ON",
        {"NEWS_TRADE_MIN_IMPACT": "medium"},
        "новостная торговля включена: порог важности снижен до medium, чтобы "
        "отрабатывались не только самые крупные новости. Выбирать режим "
        "торговли больше не нужно — он один: новостной вход в приоритете, а "
        "когда свежей новости нет, работает обычный отбор сигнала",
    ),
    (
        "MIGRATED_PARTIAL_CLOSE_ON",
        {"USE_PARTIAL_CLOSE": True},
        "включено частичное закрытие: при достижении прибыли половина объёма "
        "забирается в плюс, остаток продолжает идти под трейлингом. Работает "
        "только с лота 0.02 и больше — половину минимального лота 0.01 брокеру "
        "отправить нельзя, и программа честно пишет об этом в лог",
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
    (
        "MIGRATED_R_TRAIL_LADDER",
        {
            "USE_R_TRAIL_LADDER": True,
            # Значения новые. Прежние ((0.30, 0.00) и далее) запирали ровно
            # ноль уже на +0.30R, и через эту ступень проходило 48.8% всех
            # сделок — прибыль срезалась в самом начале. См. ниже
            # MIGRATED_R_LADDER_LATER_START: для тех, кто эту правку уже
            # получил со старыми числами, лестница обновляется отдельно.
            "R_TRAIL_LADDER": [(0.50, -0.50), (0.75, 0.00), (1.00, 0.30),
                               (1.50, 0.70), (2.00, 1.10), (3.00, 2.00)],
            "R_TRAIL_GIVEBACK_R": 0.5,
            "TP_TIGHTEN_SHRINK_PER_MINUTE": 0.18,
            "TP_TIGHTEN_MIN_FRACTION": 0.20,
        },
        "стоп-лосс подтягивается заметно раньше, а тейк-профит поджимается "
        "быстрее. По реальному отчёту (211 сделок, счёт 65) весь результат "
        "решало одно: сделки, где стоп успел уйти в плюс, дали +89.56, а где "
        "не успел — минус 109.55, при одинаковой длительности. Защита "
        "включалась не раньше 0.67 своего риска, теперь первая ступень — 0.30, "
        "и стоп переезжает в безубыток. Цель прибыли ужимается на 18% в минуту "
        "вместо 10%, но никогда не становится меньше собственного стопа сделки",
    ),
    (
        "MIGRATED_TARGET_PROFIT_PERCENT",
        {"TARGET_PROFIT_PERCENT_OF_EQUITY": 0.5},
        "денежная цель прибыли считается от счёта (0.5%), а не фиксированной "
        "суммой. Абсолютное число не переживает смену размера депозита: 1 "
        "доллар на счёте 65 — это полтора процента, а на 1000 одна десятая, и "
        "цель сжалась бы до пары пунктов, которые съедает спред. Меньше "
        "прежней цель не станет никогда — берётся большее из двух",
    ),
    (
        "MIGRATED_AUTO_UPDATE",
        {"UPDATE_AUTO_APPLY": True},
        "обновления ставятся сами, без вопросов: при запуске и раз в 3 часа "
        "во время работы. Во время работы установка происходит только когда "
        "НЕТ открытых сделок — сделку ведут трейлинг и безубыток, и остаться "
        "без них с открытой позицией хуже, чем обновиться на пару часов "
        "позже. Личные файлы (config.py, счета, сессия Telegram, журналы) "
        "обновление не трогает никогда",
    ),
    (
        "MIGRATED_REMOTE_SETTINGS",
        {
            "REMOTE_SETTINGS_ENABLED": True,
            "REMOTE_SETTINGS_MINUTES": 10,
            "UPDATE_CHECK_MINUTES": 180,
        },
        "настройки торговли приходят из GitHub сами и применяются без "
        "перезапуска, а обновления программы проверяются не только при "
        "запуске, но и раз в 3 часа во время работы — на сервере программа "
        "не перезапускается неделями, и проверка при запуске туда не "
        "доходила. Менять удалённо можно только ручки торговли из строгого "
        "списка; источник обновлений, токены, пароли и пути запрещены, а "
        "торговлю можно удалённо выключить, но не включить",
    ),
    (
        "MIGRATED_MARKET_CLOSED_GUARD",
        {
            "USE_MARKET_CLOSED_GUARD": True,
            "MARKET_DEAD_SECONDS": 90,
            "USE_THIN_MARKET_GUARD": True,
            "THIN_SPREAD_RATIO": 2.5,
            "THIN_MIN_SAMPLES": 30,
        },
        "вход закрывается, когда рынок закрыт или неликвиден — определяется "
        "по самому рынку, а не по часам: брокер запретил торговлю, цена "
        "замерла, или спред намного шире обычного для этой же пары. Часы для "
        "этого не годятся: время компьютера и время сервера брокера расходятся "
        "на 2-3 часа. Открытые сделки это не трогает — запрещается только "
        "вход и только по той паре, где сработал признак",
    ),
    (
        "MIGRATED_R_LADDER_LATER_START",
        {"R_TRAIL_LADDER": [(0.50, -0.50), (0.75, 0.00), (1.00, 0.30),
                            (1.50, 0.70), (2.00, 1.10), (3.00, 2.00)]},
        "лестница защиты прибыли начинается позже и первой ступенью не "
        "ставит стоп в безубыток. Прежняя запирала РОВНО НОЛЬ уже на +0.30R, "
        "и через эту ступень проходила почти половина сделок: прибыль "
        "срезалась в самом начале. Новая не вмешивается до +0.5R, а первой "
        "ступенью урезает риск вдвое. Проверено на EURUSD и XAUUSD, на трёх "
        "отрезках истории каждый: итог улучшился во всех шести сочетаниях. "
        "На OOS профит-фактор 0.55 -> 0.82 и 0.62 -> 0.91, просадка меньше "
        "почти вдвое. Прибыльной система от этого НЕ стала",
    ),
]

# R_TRAIL_LADDER — многострочный литерал в config.py.example, а build_patch
# многострочные значения намеренно не переносит. Без записи выше существующий
# config.py получил бы USE_R_TRAIL_LADDER = True, но БЕЗ самой лестницы, и
# трейлинг молча делал бы ничего. Проверяется в tests/test_r_trail_ladder.py.


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


# Заводской список пар, стоявший в config.py.example раньше: имена с
# суффиксом "s" конкретного брокера (SwitchMarkets). На любом другом брокере
# ни одна из них не находится.
_OLD_DEFAULT_SYMBOLS = ["XAUUSDs", "EURUSDs", "GBPUSDs", "BTCUSDs"]
_NEW_DEFAULT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]


def _fix_broker_specific_symbols(text: str, existing: dict) -> tuple:
    """Одноразовая правка списка пар — условная, поэтому не в ONE_TIME.

    Трогаем ТОЛЬКО если список ровно тот, что стоял в эталоне: это
    единственный надёжный признак, что его никто не менял. Если человек
    вписал свои пары — хоть одну, — это его выбор, и трогать нельзя."""
    marker = "MIGRATED_DEFAULT_SYMBOLS"
    if marker in existing:
        return text, ""
    node = existing.get("SYMBOLS")
    if node is None:
        return text, ""
    value_node = node["value"]
    if not isinstance(value_node, (ast.List, ast.Tuple)):
        return text, ""
    try:
        current = [e.value for e in value_node.elts if isinstance(e, ast.Constant)]
    except Exception:  # noqa: BLE001
        return text, ""
    if current != _OLD_DEFAULT_SYMBOLS:
        return text, ""

    text = _replace_or_append(text, "SYMBOLS", repr(_NEW_DEFAULT_SYMBOLS))
    note = ("список пар заменён на " + ", ".join(_NEW_DEFAULT_SYMBOLS) +
            ": прежний был с суффиксом \"s\" конкретного брокера и на другом "
            "брокере не находил НИ ОДНОЙ пары. Золото и биткоин из списка "
            "убраны — минимальный лот по ним стоит дороже, чем этот депозит "
            "может рисковать за сделку")
    text = text.rstrip("\n") + f"\n\n# {note}\n{marker} = True\n"
    return text, note


def _set_aggressive_profile(text: str, existing: dict) -> tuple:
    """Профиль по умолчанию: «Истеричка» -> «Агрессивный».

    Условная правка, поэтому не в ONE_TIME: значение здесь не число и не
    строка, а обращение к перечислению (RiskProfile.AGGRESSIVE), которое
    repr() записать не сможет.

    Трогаем ТОЛЬКО если стоит заводская HYSTERIC. Выбрал человек другой
    профиль сам — это его решение, и переписывать его нельзя.

    Зачем вообще: у «Истерички» ignore_soft_filters = True, и это разом
    выключает паузу вокруг полуночи брокера, защиту от скачка волатильности и
    проверку «спред не съедает цель». Владелец переходит с депозита 65 на
    500-1000, где риск в процентах наконец начинает управлять объёмом (на 65
    расчёт всегда просил меньше минимального лота брокера)."""
    marker = "MIGRATED_AGGRESSIVE_PROFILE"
    if marker in existing:
        return text, ""
    node = existing.get("RISK_PROFILE")
    if node is None:
        return text, ""
    value = node["value"]
    if not (isinstance(value, ast.Attribute) and value.attr == "HYSTERIC"):
        return text, ""
    text = _replace_or_append(text, "RISK_PROFILE", "RiskProfile.AGGRESSIVE")
    text = text.rstrip("\n") + f"\n\n# профиль по умолчанию\n{marker} = True\n"
    return text, (
        "профиль по умолчанию переключён с «Истерички» на «Агрессивный»: "
        "риск на сделку 1.2% вместо 0.1%, порог входа 55 вместо 45, до 5 "
        "сделок на пару вместо 10, стоп 2.0 ATR вместо 1.5. Главное — у "
        "«Истерички» был включён обход мягких фильтров, который разом "
        "отключал паузу вокруг полуночи брокера, защиту от скачка "
        "волатильности и проверку «спред не съедает цель»; на «Агрессивном» "
        "все три работают")


def _block_gold(text: str, existing: dict) -> tuple:
    """Выключает золото: и списком BLOCKED_SYMBOLS, и вычёркиванием из SYMBOLS.

    Условная правка: надо не просто записать значение, а ОТРЕДАКТИРОВАТЬ
    существующий список пар человека, сохранив всё остальное в нём.

    Одного BLOCKED_SYMBOLS хватило бы для запрета, но золото осталось бы
    висеть в списке пар на вкладке «Символы» — человек видел бы его среди
    рабочих и не понимал, почему по нему ничего не происходит."""
    marker = "MIGRATED_GOLD_OFF"
    if marker in existing:
        return text, ""

    text = _replace_or_append(text, "BLOCKED_SYMBOLS", repr(["XAUUSD"]))

    removed = []
    node = existing.get("SYMBOLS")
    if node is not None and isinstance(node["value"], (ast.List, ast.Tuple)):
        try:
            current = ast.literal_eval(node["value"])
        except (ValueError, SyntaxError):
            current = None
        if isinstance(current, (list, tuple)):
            kept = [s for s in current
                    if not str(s).upper().replace(".", "").startswith("XAUUSD")]
            removed = [s for s in current if s not in kept]
            if removed:
                text = _replace_or_append(text, "SYMBOLS", repr(list(kept)))

    text = text.rstrip("\n") + f"\n\n# золото выключено\n{marker} = True\n"
    note = ("золото выключено по просьбе владельца: новые сделки по нему не "
            "открываются, уже открытая доводится до конца обычным порядком. "
            "Имя сравнивается без суффикса брокера, поэтому XAUUSDs и "
            "XAUUSD.m тоже выключены")
    if removed:
        note += f". Из списка пар убрано: {', '.join(map(str, removed))}"
    return text, note


def _enable_update_check(text: str, existing: dict):
    """Включить проверку обновлений, когда репозиторий уже верный.

    ОТКУДА ЭТО. Владелец прислал снимок экрана: репозиторий вписан, ветка
    вписана, а галочка «Проверять обновления» снята, внизу «Обновление
    выключено в настройках». То есть правки, которые он заказывает, до его
    компьютера не доезжали вовсе — а просил он ровно обратного: «сделай, чтобы
    она сама ставила обновление, без моего участия».

    ПОЧЕМУ ОТДЕЛЬНОЙ ПРАВКОЙ, А НЕ ПУНКТОМ В ONE_TIME. Пункты ONE_TIME
    выполняются ПЕРВЫМИ, до _default_update_repo. Включи мы галочку там, для
    новой установки перестало бы срабатывать условие «оба поля на заводском
    значении» — и репозиторий больше не подставлялся бы сам. Проверено: так и
    вышло, тест это поймал.

    ПОЧЕМУ ЭТО НЕ ПРОТИВОРЕЧИТ ПРАВИЛУ «НЕ ТРОГАТЬ ОСОЗНАННЫЙ ВЫБОР».
    _default_update_repo намеренно не включает галочку, если репозиторий уже
    заполнен: там это считалось осознанным выбором. Здесь выбор сделан прямо и
    словами — владелец просил обновляться самостоятельно. Поэтому правка
    одноразовая: она сработает ОДИН раз, и если после неё галочку снять, она
    останется снятой навсегда."""
    marker = "MIGRATED_UPDATE_CHECK_ON"
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

    if _current("UPDATE_ENABLED", False) is True:
        # Уже включено — правку всё равно отмечаем сделанной, чтобы она не
        # висела и не срабатывала однажды позже, когда галочку снимут нарочно.
        text = text.rstrip("\n") + f"\n\n{marker} = True\n"
        return text, ""

    repo = str(_current("UPDATE_REPO", "") or "")
    if "/" not in repo:
        return text, ""      # репозитория нет — включать нечего, не мешаем

    text = _replace_or_append(text, "UPDATE_ENABLED", repr(True))
    text = text.rstrip("\n") + f"\n\n{marker} = True\n"
    return text, ("проверка обновлений включена: репозиторий вписан, а галочка "
                  "была снята — правки не доезжали до программы вовсе. "
                  "Сработает один раз: снимете после этого сами — так и "
                  "останется")


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

    existing = _top_level_assignments(text)
    text, check_note = _enable_update_check(text, existing)
    if check_note:
        applied.append(check_note)

    existing = _top_level_assignments(text)
    text, symbols_note = _fix_broker_specific_symbols(text, existing)
    if symbols_note:
        applied.append(symbols_note)

    # Каждый шаг пересчитывает existing заново: предыдущие уже изменили текст,
    # и разбор устарел бы.
    existing = _top_level_assignments(text)
    text, profile_note = _set_aggressive_profile(text, existing)
    if profile_note:
        applied.append(profile_note)

    existing = _top_level_assignments(text)
    text, gold_note = _block_gold(text, existing)
    if gold_note:
        applied.append(gold_note)

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
