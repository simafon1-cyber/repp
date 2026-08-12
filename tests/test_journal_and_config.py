#!/usr/bin/env python3
"""Тесты по трём жалобам владельца программы:

  1. «Некорректные значения в полях: PROFIT_LOCK_START_R_FRACTION, ...»
     при попытке сохранить настройки риска.
     Причина: параметр появился в новой версии, а в личном config.py его нет —
     поле показывалось пустым, и пустоту нельзя превратить в число.
     Проверяем config_migrate (дописывает недостающее) и запасной путь в
     интерфейсе (подстановка значения по умолчанию).

  2. «Удалить порог убытка» — дневной лимит убытка больше не должен
     останавливать бота до завтра. Проверяем, что он выключен и что 0 в
     профиле честно означает «порога нет», а не «стоп при любом минусе».

  3. «Не читается шрифт» — названия новостей как «??????».
     Причина не в шрифте: сервис календаря писал файл в однобайтовой
     кодировке, где нет русских букв. Проверяем, что запись теперь в UTF-8 и
     что программа умеет распознать старый испорченный файл и сказать об этом.

Плюс — журнал сделок в облаке (cloud_journal): что он считает, что пишет и,
главное, чего он НИКОГДА не отправляет наружу.

Запуск:  python3 tests/test_journal_and_config.py
"""

from __future__ import annotations

import ast
import os
import re
import sys
import tempfile
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
APP = ROOT / "ai_scalper_standalone"
sys.path.insert(0, str(APP))

passed = 0
failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  OK   {name}")
    else:
        failed += 1
        print(f"  СБОЙ {name}" + (f"  -> {detail}" if detail else ""))


# Заглушки: настоящий config.py в git не хранится (там ключи), а MetaTrader5
# на Linux не ставится. Настройки берём из config.py.example — ровно те, с
# которыми программа приедет к пользователю.
cfg = types.ModuleType("config")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg
CFG = cfg


class _FakeMT5(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return name


_mt5 = _FakeMT5("MetaTrader5")
_mt5.initialize = lambda *a, **k: False
_mt5.symbol_info = lambda *a, **k: None
sys.modules["MetaTrader5"] = _mt5

import cloud_journal as cj        # noqa: E402
import config_migrate as cm       # noqa: E402
import news_providers as npv      # noqa: E402
import risk_manager as rm         # noqa: E402
import secure_store as ss         # noqa: E402


def code_only(text: str) -> str:
    """Текст файла без комментариев и строк документации: чтобы проверка не
    срабатывала на описание ошибки в комментарии вместо самого кода."""
    out = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    doc_positions = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            doc_positions.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    for i, line in enumerate(text.splitlines(), start=1):
        if i in doc_positions:
            continue
        out.append(line.split("#", 1)[0])
    return "\n".join(out)


# =====================================================================
# 1. Пустые поля настроек
# =====================================================================
def test_config_migrate_adds_missing() -> None:
    print("\n[Новые настройки дописываются в config.py]")
    example = (APP / "config.py.example").read_text(encoding="utf-8")

    # Эталон сам себе ничего не должен добавлять
    check(cm.missing_keys(example, example) == [],
          "Эталон полон: сам себе ничего не дописывает")

    # Старый конфиг без новых параметров — ровно та ситуация из жалобы
    new_keys = ["PROFIT_LOCK_START_R_FRACTION", "POSITION_MONITOR_SECONDS",
                "TP_TIGHTEN_MIN_FRACTION", "TP_LEARN_FRACTION",
                "BE_RESCUE_AFTER_MINUTES"]
    old = "\n".join(line for line in example.splitlines()
                    if not any(line.startswith(k) for k in new_keys))
    missing = cm.missing_keys(old, example)
    for key in new_keys:
        check(key in missing, f"Замечен пропавший параметр {key}")

    with tempfile.TemporaryDirectory() as d:
        config_path = os.path.join(d, "config.py")
        example_path = os.path.join(d, "example")
        Path(config_path).write_text(old, encoding="utf-8")
        Path(example_path).write_text(example, encoding="utf-8")

        added = cm.sync(config_path, example_path)
        check(sorted(added) == sorted(missing), "Дописано ровно недостающее")

        result = types.ModuleType("after")
        exec(Path(config_path).read_text(encoding="utf-8"), result.__dict__)
        for key in new_keys:
            check(hasattr(result, key), f"После правки {key} есть в config.py")

        # Второй запуск не должен ничего менять
        before = Path(config_path).read_text(encoding="utf-8")
        check(cm.sync(config_path, example_path) == [],
              "Повторный запуск ничего не добавляет")
        check(Path(config_path).read_text(encoding="utf-8") == before,
              "Файл после повторного запуска не изменился")


def test_config_migrate_never_overwrites() -> None:
    print("\n[Личные настройки не затираются]")
    example = "RISK = 1.0\nSYMBOLS = ['EURUSD']\nNEWKEY = 7\n"
    mine = "RISK = 0.15\nSYMBOLS = ['XAUUSD', 'GBPUSD']\n"

    with tempfile.TemporaryDirectory() as d:
        config_path = os.path.join(d, "config.py")
        example_path = os.path.join(d, "example")
        Path(config_path).write_text(mine, encoding="utf-8")
        Path(example_path).write_text(example, encoding="utf-8")
        cm.sync(config_path, example_path)

        after = types.ModuleType("after")
        exec(Path(config_path).read_text(encoding="utf-8"), after.__dict__)
        check(after.RISK == 0.15, "Свой риск остался нетронутым", str(after.RISK))
        check(after.SYMBOLS == ["XAUUSD", "GBPUSD"], "Свой список пар остался",
              str(after.SYMBOLS))
        check(after.NEWKEY == 7, "Новый параметр добавлен")


def test_config_migrate_clears_stale_update_branch() -> None:
    """Раньше пустое поле «Ветка» при сохранении настроек ПРИНУДИТЕЛЬНО
    заменялось на "main" — если такой ветки в репозитории нет (обычное дело,
    пока не сделан ни один Pull Request), обновление отвечало «Репозиторий
    или ветка не найдены» на каждый файл. Одноразовая миграция должна
    очистить именно этот уже-сохранённый принудительный "main", чтобы
    сработало автоопределение ветки (updater.repo_default_branch())."""
    print("\n[Застрявшая ветка обновления «main» очищается один раз]")

    with tempfile.TemporaryDirectory() as d:
        config_path = os.path.join(d, "config.py")
        Path(config_path).write_text(
            'UPDATE_ENABLED = True\nUPDATE_REPO = "a/b"\nUPDATE_BRANCH = "main"\n',
            encoding="utf-8")
        applied = cm.apply_one_time(config_path)
        check(any("Ветка" in a for a in applied),
              "Миграция отработала и объяснила, что сделала", str(applied))

        after = types.ModuleType("after")
        exec(Path(config_path).read_text(encoding="utf-8"), after.__dict__)
        check(after.UPDATE_BRANCH == "",
              "UPDATE_BRANCH очищена -> сработает автоопределение",
              repr(after.UPDATE_BRANCH))
        check(after.UPDATE_REPO == "a/b", "Остальные настройки не тронуты")

        # Повторный запуск больше ничего не делает с этим полем
        before = Path(config_path).read_text(encoding="utf-8")
        cm.apply_one_time(config_path)
        check(Path(config_path).read_text(encoding="utf-8") == before,
              "Повторный запуск идемпотентен")

    # Осознанно выбранная ветка — трогать нельзя ни в коем случае
    with tempfile.TemporaryDirectory() as d:
        config_path = os.path.join(d, "config.py")
        Path(config_path).write_text('UPDATE_BRANCH = "develop"\n', encoding="utf-8")
        cm.apply_one_time(config_path)
        after = types.ModuleType("after2")
        exec(Path(config_path).read_text(encoding="utf-8"), after.__dict__)
        check(after.UPDATE_BRANCH == "develop",
              "Явно заданная НЕ-main ветка не трогается миграцией")

    # Отсутствующая настройка (совсем старый конфиг) не должна ронять миграцию
    with tempfile.TemporaryDirectory() as d:
        config_path = os.path.join(d, "config.py")
        Path(config_path).write_text('X = 1\n', encoding="utf-8")
        applied = cm.apply_one_time(config_path)
        check(not any("Ветка" in a for a in applied),
              "Без UPDATE_BRANCH в файле — миграции ветки просто нечего делать")


def test_config_migrate_defaults_update_repo() -> None:
    """«Пропиши все по умолчанию»: полностью нетронутая настройка обновления
    (UPDATE_REPO="" и UPDATE_ENABLED=False — оба на старом заводском значении)
    получает репозиторий программы и включённую проверку сама, без ручного
    ввода. Если человек уже что-то настроил сам (свой репозиторий или явно
    выключил проверку) — миграция не имеет права это перезаписать."""
    print("\n[Обновление настраивается по умолчанию само]")

    with tempfile.TemporaryDirectory() as d:
        config_path = os.path.join(d, "config.py")
        Path(config_path).write_text(
            'UPDATE_ENABLED = False\nUPDATE_REPO = ""\nOTHER_SETTING = 1\n',
            encoding="utf-8")
        applied = cm.apply_one_time(config_path)
        check(any("самообновление включено" in a for a in applied),
              "Миграция отработала", str(applied))

        after = types.ModuleType("after")
        exec(Path(config_path).read_text(encoding="utf-8"), after.__dict__)
        check(after.UPDATE_REPO == "simafon1-cyber/repp",
              "Репозиторий заполнен сам", after.UPDATE_REPO)
        check(after.UPDATE_ENABLED is True, "Проверка включена сама")
        check(after.OTHER_SETTING == 1, "Посторонняя настройка не тронута")

        before = Path(config_path).read_text(encoding="utf-8")
        cm.apply_one_time(config_path)
        check(Path(config_path).read_text(encoding="utf-8") == before,
              "Повторный запуск идемпотентен")

    # Свой репозиторий — не трогаем, даже если проверка выключена
    with tempfile.TemporaryDirectory() as d:
        config_path = os.path.join(d, "config.py")
        Path(config_path).write_text(
            'UPDATE_ENABLED = False\nUPDATE_REPO = "кто-то/чужой-форк"\n',
            encoding="utf-8")
        cm.apply_one_time(config_path)
        after = types.ModuleType("after2")
        exec(Path(config_path).read_text(encoding="utf-8"), after.__dict__)
        check(after.UPDATE_REPO == "кто-то/чужой-форк",
              "Чужой репозиторий не переписан", after.UPDATE_REPO)

    # ПРАВИЛО ИЗМЕНИЛОСЬ, И НАМЕРЕННО. Раньше здесь проверялось обратное:
    # «репозиторий верный, галочка снята — не трогаем, это осознанный выбор».
    # Владелец прислал снимок ровно такого состояния: репозиторий вписан,
    # галочка снята, «Обновление выключено в настройках» — то есть заказанные
    # им правки не доезжали до программы вовсе, при том что просил он
    # «чтобы она сама ставила обновление, без моего участия».
    #
    # Теперь галочка включается ОДИН раз. Снятая после этого — остаётся снятой
    # навсегда: именно это и делает выбор осознанным, а не угаданным.
    with tempfile.TemporaryDirectory() as d:
        config_path = os.path.join(d, "config.py")
        Path(config_path).write_text(
            'UPDATE_ENABLED = False\nUPDATE_REPO = "simafon1-cyber/repp"\n',
            encoding="utf-8")
        applied = cm.apply_one_time(config_path)
        after = types.ModuleType("after3")
        exec(Path(config_path).read_text(encoding="utf-8"), after.__dict__)
        check(after.UPDATE_ENABLED is True,
              "Галочка включается: без неё правки не доезжают до программы")
        check(any("не доезжали" in a for a in applied),
              "И человеку сказано, почему её тронули", str(applied)[:120])

        # Второй раз — уже не лезем: человек снял сам, значит так и надо
        Path(config_path).write_text(
            Path(config_path).read_text(encoding="utf-8")
            .replace("UPDATE_ENABLED = True", "UPDATE_ENABLED = False"),
            encoding="utf-8")
        cm.apply_one_time(config_path)
        after = types.ModuleType("after3b")
        exec(Path(config_path).read_text(encoding="utf-8"), after.__dict__)
        check(after.UPDATE_ENABLED is False,
              "СНЯТУЮ ПОСЛЕ ЭТОГО — не включаем обратно никогда")

    # Уже включено — не рапортуем о том, чего не делали. Строка «проверка
    # обновлений включена» в списке изменений означает, что программа что-то
    # поменяла; если она появляется на пустом месте, человек перестаёт верить
    # всему списку.
    with tempfile.TemporaryDirectory() as d:
        config_path = os.path.join(d, "config.py")
        Path(config_path).write_text(
            'UPDATE_ENABLED = True\nUPDATE_REPO = "simafon1-cyber/repp"\n',
            encoding="utf-8")
        applied = cm.apply_one_time(config_path)
        check(not any("проверка обновлений включена" in a for a in applied),
              "Про уже включённую галочку не сообщается как об изменении",
              str([a for a in applied if "обновлен" in a])[:120])
        after = types.ModuleType("after3d")
        exec(Path(config_path).read_text(encoding="utf-8"), after.__dict__)
        check(after.UPDATE_ENABLED is True, "И она осталась включённой")

    # Репозитория нет вовсе — включать нечего, не мешаем
    with tempfile.TemporaryDirectory() as d:
        config_path = os.path.join(d, "config.py")
        Path(config_path).write_text(
            'UPDATE_ENABLED = False\nUPDATE_REPO = "мусор-без-косой-черты"\n',
            encoding="utf-8")
        cm.apply_one_time(config_path)
        after = types.ModuleType("after3c")
        exec(Path(config_path).read_text(encoding="utf-8"), after.__dict__)
        check(after.UPDATE_ENABLED is False,
              "Без пригодного репозитория галочку не включаем — толку нет")

    # Отсутствие настроек вовсе (совсем старый файл) не роняет миграцию
    with tempfile.TemporaryDirectory() as d:
        config_path = os.path.join(d, "config.py")
        Path(config_path).write_text('X = 1\n', encoding="utf-8")
        applied = cm.apply_one_time(config_path)
        check(any("самообновление включено" in a for a in applied),
              "Совсем старый файл (без UPDATE_*) тоже получает значения по умолчанию",
              str(applied))


def test_config_migrate_widens_stale_stop_loss() -> None:
    """Владелец: «сделай стоп лосс больше, очень много убытка».

    RISK_PROFILES — многострочный словарь, обычная миграция (sync) его не
    трогает вовсе (см. SKIP). Одноразовая миграция обязана раздвинуть
    atr_sl_multiplier в УЖЕ существующем config.py пользователя — иначе
    новые (широкие) значения попадут только в config.py.example и никогда не
    доедут до работающей программы."""
    print("\n[Стоп-лосс расширяется в уже существующем config.py]")

    old_config = """from enum import Enum

class RiskProfile(Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"
    HYSTERIC = "hysteric"

RISK_PROFILES = {
    RiskProfile.CONSERVATIVE: dict(
        risk_percent=0.3, atr_sl_multiplier=1.0, use_money_tp=True, target_profit_money=2.0,
        min_score_to_trade=70, max_open_positions=1, max_trades_per_day=0,
        daily_loss_limit_pct=2.0, max_drawdown_pct=6.0, max_total_risk_pct=0.5,
        ignore_soft_filters=False, hedge_both_directions=False, name="Консервативный",
    ),
    RiskProfile.BALANCED: dict(
        risk_percent=0.7, atr_sl_multiplier=1.2, use_money_tp=True, target_profit_money=4.0,
        min_score_to_trade=62, max_open_positions=2, max_trades_per_day=0,
        daily_loss_limit_pct=3.0, max_drawdown_pct=10.0, max_total_risk_pct=1.8,
        ignore_soft_filters=False, hedge_both_directions=False, name="Сбалансированный",
    ),
    RiskProfile.AGGRESSIVE: dict(
        risk_percent=1.2, atr_sl_multiplier=0.8, use_money_tp=True, target_profit_money=8.0,
        min_score_to_trade=55, max_open_positions=5, max_trades_per_day=0,
        daily_loss_limit_pct=5.0, max_drawdown_pct=15.0, max_total_risk_pct=6.5,
        ignore_soft_filters=False, hedge_both_directions=False, name="Агрессивный",
    ),
    RiskProfile.HYSTERIC: dict(
        risk_percent=0.1, atr_sl_multiplier=0.5, use_money_tp=True, target_profit_money=1.0,
        min_score_to_trade=45, max_open_positions=10, max_trades_per_day=0,
        daily_loss_limit_pct=8.0, max_drawdown_pct=25.0, max_total_risk_pct=3.0,
        ignore_soft_filters=True, hedge_both_directions=False, name="Истеричка (YOLO)",
    ),
}
MIN_SL_SPREAD_MULTIPLE = 4.0
MIN_SL_ATR_FRACTION = 0.8
MY_CUSTOM_SETTING = "не трогать"
"""
    with tempfile.TemporaryDirectory() as d:
        config_path = os.path.join(d, "config.py")
        Path(config_path).write_text(old_config, encoding="utf-8")
        applied = cm.apply_one_time(config_path)
        check(any("стоп-лосс расширен" in a for a in applied),
              "Миграция профилей риска отработала", str(applied))
        check(any("минимальная дистанция стопа" in a for a in applied),
              "Миграция MIN_SL_* отработала", str(applied))

        after = types.ModuleType("after")
        exec(Path(config_path).read_text(encoding="utf-8"), after.__dict__)
        expected = {"CONSERVATIVE": 2.5, "BALANCED": 2.5, "AGGRESSIVE": 2.0,
                   "HYSTERIC": 1.5}
        for enum_member, params in after.RISK_PROFILES.items():
            want = expected[enum_member.name]
            check(abs(params["atr_sl_multiplier"] - want) < 1e-9,
                  f"{enum_member.name}: atr_sl_multiplier расширен до {want}",
                  str(params["atr_sl_multiplier"]))
            # Остальные поля профиля не должны были измениться
            check(params["risk_percent"] > 0, f"{enum_member.name}: risk_percent на месте")

        check(after.MIN_SL_SPREAD_MULTIPLE == 8.0, "MIN_SL_SPREAD_MULTIPLE расширен")
        check(after.MIN_SL_ATR_FRACTION == 1.5, "MIN_SL_ATR_FRACTION расширен")
        check(after.MY_CUSTOM_SETTING == "не трогать", "Посторонняя настройка не тронута")

        # Идемпотентность
        before_text = Path(config_path).read_text(encoding="utf-8")
        cm.apply_one_time(config_path)
        check(Path(config_path).read_text(encoding="utf-8") == before_text,
              "Повторный запуск ничего не меняет")


def test_config_migrate_does_not_touch_customized_stop_loss() -> None:
    print("\n[Осознанно настроенный стоп-лосс не трогается]")
    custom_config = """from enum import Enum

class RiskProfile(Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"

RISK_PROFILES = {
    RiskProfile.CONSERVATIVE: dict(
        risk_percent=0.3, atr_sl_multiplier=3.3, use_money_tp=True, target_profit_money=2.0,
        min_score_to_trade=70, max_open_positions=1, max_trades_per_day=0,
        daily_loss_limit_pct=2.0, max_drawdown_pct=6.0, max_total_risk_pct=0.5,
        ignore_soft_filters=False, hedge_both_directions=False, name="Мой",
    ),
    RiskProfile.BALANCED: dict(
        risk_percent=0.7, atr_sl_multiplier=1.2, use_money_tp=True, target_profit_money=4.0,
        min_score_to_trade=62, max_open_positions=2, max_trades_per_day=0,
        daily_loss_limit_pct=3.0, max_drawdown_pct=10.0, max_total_risk_pct=1.8,
        ignore_soft_filters=False, hedge_both_directions=False, name="Сбалансированный",
    ),
}
MIN_SL_SPREAD_MULTIPLE = 12.5
"""
    with tempfile.TemporaryDirectory() as d:
        config_path = os.path.join(d, "config.py")
        Path(config_path).write_text(custom_config, encoding="utf-8")
        cm.apply_one_time(config_path)

        after = types.ModuleType("after")
        exec(Path(config_path).read_text(encoding="utf-8"), after.__dict__)
        for enum_member, params in after.RISK_PROFILES.items():
            if enum_member.name == "CONSERVATIVE":
                check(params["atr_sl_multiplier"] == 3.3,
                      "Изменённое значение CONSERVATIVE осталось своим",
                      str(params["atr_sl_multiplier"]))
            elif enum_member.name == "BALANCED":
                check(params["atr_sl_multiplier"] == 2.5,
                      "Нетронутый (заводской) BALANCED всё же расширен",
                      str(params["atr_sl_multiplier"]))
        check(after.MIN_SL_SPREAD_MULTIPLE == 12.5,
              "Изменённый MIN_SL_SPREAD_MULTIPLE не тронут")


def test_config_migrate_fresh_example_needs_no_stop_loss_migration() -> None:
    """config.py.example уже на новых (широких) значениях — свежая установка
    не должна получать лишнюю пометку "стоп-лосс расширен" на пустом месте."""
    print("\n[Свежий эталон не запускает миграцию стоп-лосса]")
    example = (APP / "config.py.example").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as d:
        config_path = os.path.join(d, "config.py")
        Path(config_path).write_text(example, encoding="utf-8")
        applied = cm.apply_one_time(config_path)
        stop_related = [a for a in applied
                        if "стоп-лосс расширен" in a or "минимальная дистанция" in a]
        check(stop_related == [], "Эталон уже широкий — миграция ничего не делает",
              str(stop_related))


def test_config_migrate_skips_multiline() -> None:
    print("\n[Сложные блоки не трогаются]")
    example = (APP / "config.py.example").read_text(encoding="utf-8")
    missing = cm.missing_keys("", example)
    check("RISK_PROFILES" not in missing,
          "Многострочный RISK_PROFILES не переносится вслепую")
    check("MARKET_CONTEXT" not in missing,
          "Многострочный MARKET_CONTEXT не переносится вслепую")

    # Ни одна дописанная строка не должна ломать синтаксис
    patch = cm.build_patch("", example)
    try:
        ast.parse(patch)
        ok = True
    except SyntaxError as e:
        ok = False
        detail = str(e)
    check(ok, "Дописываемый кусок — валидный Python", locals().get("detail", ""))


def test_ui_falls_back_to_default() -> None:
    print("\n[Пустых полей в настройках не остаётся]")
    source = (APP / "desktop_app.py").read_text(encoding="utf-8")
    body = code_only(source)

    check("def param_current_value" in body,
          "Есть отдельная функция подстановки значения")
    check('current = param_current_value(key)' in body,
          "Поля вкладки «Настройка» заполняются через неё")
    check('current = getattr(cfg, key, "")' not in body,
          "Старая подстановка пустой строки убрана")
    # «Обновить из файла» не должно возвращать поля в пустое состояние —
    # иначе ошибка сохранения вернулась бы одним нажатием кнопки
    reload_fn = body.split("def reload_advanced_params", 1)[1].split("\n    def ", 1)[0]
    check("param_current_value(key)" in reload_fn,
          "«Обновить из файла» тоже подставляет значение по умолчанию")
    check("SECRET_PLACEHOLDER" in reload_fn,
          "«Обновить из файла» не выкладывает секреты на экран")

    # Сохранение: пустое число заменяется значением по умолчанию, а не
    # роняет сохранение ВСЕХ параметров
    save = body.split("def save_advanced_params", 1)[1].split("\n    def ", 1)[0]
    check('ptype in ("int", "float")' in save and "param_help.default_of" in save,
          "При сохранении пустое число берёт значение по умолчанию")
    check("continue" in body.split("SECRET_PLACEHOLDER", 1)[1][:400] or
          'ptype == "secret"' in save,
          "Секреты по-прежнему трактуются как «пусто = не менять»")


def test_migration_runs_on_start() -> None:
    print("\n[Правка настроек происходит при запуске]")
    body = code_only((APP / "desktop_app.py").read_text(encoding="utf-8"))
    start = body.split("def main(", 1)[1]
    check("config_migrate.sync()" in start,
          "Недостающие настройки дописываются при старте")
    check("config_migrate.apply_one_time()" in start,
          "Одноразовые изменения применяются при старте")


# =====================================================================
# 2. Порог убытка снят
# =====================================================================
class FakeAccount:
    def __init__(self, day_start_equity=100.0, peak_equity=100.0):
        self.day_start_equity = day_start_equity
        self.peak_equity = peak_equity


def test_daily_loss_limit_off() -> None:
    print("\n[Дневной порог убытка снят]")
    check(CFG.USE_DAILY_LOSS_LIMIT is False,
          "По умолчанию порог выключен", str(CFG.USE_DAILY_LOSS_LIMIT))

    # Даже крупный минус за день не должен останавливать торговлю
    acc = FakeAccount(day_start_equity=100.0)
    check(rm.daily_loss_limit_hit(acc, 80.0) is False,
          "Минус 20% за день не останавливает бота")

    # Включили обратно — работает как раньше
    old = CFG.USE_DAILY_LOSS_LIMIT
    CFG.USE_DAILY_LOSS_LIMIT = True
    profile = rm.get_profile()
    limit = abs(profile["daily_loss_limit_pct"])
    check(rm.daily_loss_limit_hit(acc, 100.0 - limit - 1) is True,
          "Включённый порог по-прежнему срабатывает")
    check(rm.daily_loss_limit_hit(acc, 100.0 - limit / 2) is False,
          "И не срабатывает раньше времени")
    CFG.USE_DAILY_LOSS_LIMIT = old


def test_zero_means_no_limit() -> None:
    print("\n[Ноль означает «порога нет», а не «стоп при любом минусе»]")
    old_flag = CFG.USE_DAILY_LOSS_LIMIT
    profile = rm.get_profile()
    old_daily = profile["daily_loss_limit_pct"]
    old_dd = profile["max_drawdown_pct"]

    CFG.USE_DAILY_LOSS_LIMIT = True
    profile["daily_loss_limit_pct"] = 0.0
    acc = FakeAccount(day_start_equity=100.0)
    check(rm.daily_loss_limit_hit(acc, 99.99) is False,
          "0% дневного порога = порога нет")
    check(rm.daily_loss_limit_hit(acc, 10.0) is False,
          "0% не срабатывает даже при огромном минусе")

    profile["max_drawdown_pct"] = 0.0
    acc2 = FakeAccount(peak_equity=100.0)
    check(rm.max_drawdown_hit(acc2, 99.99) is False,
          "0% просадки = лимита нет")

    profile["daily_loss_limit_pct"] = old_daily
    profile["max_drawdown_pct"] = old_dd
    CFG.USE_DAILY_LOSS_LIMIT = old_flag


def test_nothing_halts_trading() -> None:
    """Владелец: «Не останавливать торговлю, убрать это условие».

    Проверяем ГЛАВНОЕ утверждение целиком: ни одно из трёх условий, которые
    раньше выключали бота, больше не срабатывает при настройках по умолчанию.
    Не по отдельности, а через ту самую функцию, которую спрашивает торговый
    цикл перед каждым входом."""
    print("\n[Ничто не останавливает торговлю]")
    from state import SymbolState

    check(CFG.USE_DAILY_LOSS_LIMIT is False, "Дневной порог убытка выключен")
    check(CFG.USE_MAX_DRAWDOWN_LIMIT is False, "Лимит просадки выключен")
    check(int(CFG.PAUSE_MINUTES_AFTER_LOSS_STREAK) == 0,
          "Пауза после серии убытков снята",
          str(CFG.PAUSE_MINUTES_AFTER_LOSS_STREAK))

    sym = SymbolState("XAUUSD")
    sym.consecutive_losses = 99

    # Счёт наполовину съеден за день и просел от пика — торговля продолжается
    acc = FakeAccount(day_start_equity=100.0, peak_equity=200.0)
    for equity in (99.0, 80.0, 50.0, 10.0):
        check(rm.trading_allowed(acc, sym, equity) is True,
              f"Эквити {equity} при старте дня 100 и пике 200 — бот работает")

    check(rm.loss_streak_pause_minutes() == 0.0,
          "Длительность паузы — ноль", str(rm.loss_streak_pause_minutes()))


def test_trade_risk_cap_blocks_too_expensive_symbol() -> None:
    """Разбор РЕАЛЬНОГО отчёта владельца (счёт $65.26, профиль «Истеричка»,
    risk_percent 0.1%): золото минимальным лотом 0.01 рисковало 6.9% счёта за
    сделку — в 69 раз больше настроенного. 37 сделок по золоту дали -29.56 при
    общем убытке -34.74, то есть 85% всех потерь на ОДНОМ инструменте. Все
    остальные пары вместе потеряли -5.18, а GBPUSD был в плюсе.

    Причина не в сигнале, а в размере: ниже минимального лота брокера
    опуститься нельзя, поэтому risk_percent на таком депозите просто
    перестаёт действовать. Потолок MAX_TRADE_RISK_PERCENT_OF_EQUITY отсекает
    инструмент, который депозиту не по размеру, и при этом НЕ мешает торговать
    тем, что помещается."""
    print("\n[Слишком дорогой инструмент не торгуется на малом депозите]")
    from state import SymbolState
    import control as ctl

    saved_info = rm._symbol_info
    saved_override = ctl.control.get_lot_override
    saved_cap = getattr(CFG, "MAX_TRADE_RISK_PERCENT_OF_EQUITY", 2.0)
    ctl.control.get_lot_override = lambda s: 0

    class Gold:      # 1 лот = 100 унций: шаг 0.01 цены стоит 1$
        volume_min = 0.01; volume_max = 100.0; volume_step = 0.01
        trade_tick_value = 1.0; trade_tick_size = 0.01

    class FX:        # 1 лот = 100 000: шаг 0.00001 стоит 1$
        volume_min = 0.01; volume_max = 100.0; volume_step = 0.01
        trade_tick_value = 1.0; trade_tick_size = 0.00001

    rm._symbol_info = lambda s: Gold() if s == "XAUUSD" else FX()
    try:
        CFG.MAX_TRADE_RISK_PERCENT_OF_EQUITY = 2.0
        equity = 65.26     # ровно как в отчёте владельца

        # Золото: минимальный лот рискует 4.50 = 6.9% -> отказ
        st = SymbolState("XAUUSD")
        check(rm.calc_lot("XAUUSD", 4.5, equity, st) == 0.0,
              "Золото при стопе 4.5 не торгуется (6.9% счёта за сделку)")
        check("отменена" in st.last_risk_warning,
              "Причина отказа записана", st.last_risk_warning)
        check("6.9" in st.last_risk_warning and "2.0" in st.last_risk_warning,
              "В причине названы и реальный риск, и потолок", st.last_risk_warning)

        # И со СТАРЫМ, узким стопом тоже: дело не в стопе, а в размере лота
        st2 = SymbolState("XAUUSD")
        check(rm.calc_lot("XAUUSD", 2.0, equity, st2) == 0.0,
              "И со старым узким стопом золото всё равно не по размеру (3.1%)")

        # Валютные пары помещаются в потолок и торгуются как раньше
        st3 = SymbolState("EURUSD")
        lot = rm.calc_lot("EURUSD", 0.00030, equity, st3)
        check(lot > 0, "EURUSD торгуется (0.5% счёта)", str(lot))

        st4 = SymbolState("EURUSD")
        check(rm.calc_lot("EURUSD", 0.00110, equity, st4) > 0,
              "И с более широким стопом тоже (1.7% — под потолком)")

        # На БОЛЬШОМ счёте золото снова доступно: потолок в процентах, а не
        # запрет инструмента навсегда
        st5 = SymbolState("XAUUSD")
        check(rm.calc_lot("XAUUSD", 4.5, 1000.0, st5) > 0,
              "На счёте $1000 золото торгуется — потолок относительный")

        # Потолок 0 = выключен (для тех, кто хочет как раньше)
        CFG.MAX_TRADE_RISK_PERCENT_OF_EQUITY = 0
        st6 = SymbolState("XAUUSD")
        check(rm.calc_lot("XAUUSD", 4.5, equity, st6) > 0,
              "Потолок 0 выключает проверку — поведение как раньше")
    finally:
        rm._symbol_info = saved_info
        ctl.control.get_lot_override = saved_override
        CFG.MAX_TRADE_RISK_PERCENT_OF_EQUITY = saved_cap


def test_trade_risk_cap_reason_reaches_interface() -> None:
    print("\n[Причина отказа доходит до интерфейса, а не только в журнал]")
    body = code_only((APP / "main.py").read_text(encoding="utf-8"))
    block = body.split("lot = rm.calc_lot", 1)[1][:600]
    check("last_risk_warning" in block,
          "Показывается точная причина из calc_lot, а не общая фраза")

    fresh = types.ModuleType("fresh_cap")
    exec((APP / "config.py.example").read_text(encoding="utf-8"), fresh.__dict__)
    check(float(fresh.MAX_TRADE_RISK_PERCENT_OF_EQUITY) == 2.0,
          "Потолок включён по умолчанию",
          str(fresh.MAX_TRADE_RISK_PERCENT_OF_EQUITY))


def test_risk_is_capped_per_trade() -> None:
    """Остановки убраны — значит защита должна работать НА КАЖДОЙ сделке.
    Если и это исчезнет, счёт останется без ограничений вообще."""
    print("\n[Вместо остановки ограничен размер каждого убытка]")
    profile = CFG.RISK_PROFILES[CFG.RiskProfile(CFG.RISK_PROFILE)]
    check(float(profile["risk_percent"]) > 0,
          "Риск на сделку ограничен процентом от счёта",
          str(profile["risk_percent"]))
    check(float(profile["max_total_risk_pct"]) > 0,
          "Совокупный риск по всем открытым сделкам ограничен",
          str(profile["max_total_risk_pct"]))

    risk_src = (APP / "risk_manager.py").read_text(encoding="utf-8")
    check("def apply_min_stop_floor" in risk_src,
          "Стоп не может оказаться внутри спреда и шума")
    check(float(getattr(CFG, "MIN_SL_SPREAD_MULTIPLE", 0)) > 0,
          "Минимум по спреду задан", str(getattr(CFG, "MIN_SL_SPREAD_MULTIPLE", 0)))
    check(float(getattr(CFG, "MIN_SL_ATR_FRACTION", 0)) > 0,
          "Минимум по размаху свечи задан",
          str(getattr(CFG, "MIN_SL_ATR_FRACTION", 0)))

    # Снижение объёма по серии убытков — единственное, что осталось реагировать
    # на серию. Без него отмена паузы означала бы полный объём после любой
    # череды неудач.
    check(CFG.USE_LOSS_STREAK_RISK_SCALING is True,
          "Объём снижается по мере серии убытков")
    from state import SymbolState
    sym = SymbolState("XAUUSD")
    sym.consecutive_losses = 0
    check(rm.loss_streak_risk_multiplier(sym) == 1.0, "Без убытков — полный объём")
    sym.consecutive_losses = CFG.MAX_CONSECUTIVE_LOSSES
    at_limit = rm.loss_streak_risk_multiplier(sym)
    check(at_limit <= CFG.MIN_LOSS_STREAK_RISK_MULTIPLIER + 1e-9,
          "На пороге серии объём урезан до минимума", str(at_limit))
    sym.consecutive_losses = CFG.MAX_CONSECUTIVE_LOSSES * 10
    check(rm.loss_streak_risk_multiplier(sym) >= CFG.MIN_LOSS_STREAK_RISK_MULTIPLIER,
          "И не уходит ниже заданного минимума ни при какой серии")


def test_loss_counter_not_reset_without_pause() -> None:
    """Раньше счётчик убытков обнулялся вместе с постановкой паузы. Если паузы
    нет, а счётчик всё равно обнулять, то после пятого убытка подряд бот
    вернулся бы к ПОЛНОМУ объёму — то есть остался бы и без паузы, и без
    снижения риска. Проверяем, что этого не происходит."""
    print("\n[Без паузы счётчик убытков не обнуляется]")
    # Разбираем дерево, а не текст: «else» в тексте ниже принадлежит проверке
    # «убыток или прибыль», и поиск подстрокой перепутал бы ветки.
    tree = ast.parse((APP / "main.py").read_text(encoding="utf-8"))
    target = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and ast.dump(node.test).count("pause_minutes") == 1
                and isinstance(node.test.ops[0], ast.Gt)):
            target = node
            break
    check(target is not None, "Найдена проверка «пауза вообще задана»")
    if target is None:
        return

    def resets_counter(nodes) -> bool:
        for n in nodes:
            for sub in ast.walk(n):
                if (isinstance(sub, ast.Assign)
                        and isinstance(sub.value, ast.Constant) and sub.value.value == 0
                        and any(isinstance(t, ast.Attribute)
                                and t.attr == "consecutive_losses"
                                for t in sub.targets)):
                    return True
        return False

    check(resets_counter(target.body),
          "С паузой счётчик обнуляется, как и раньше")
    check(not resets_counter(target.orelse),
          "Без паузы счётчик остаётся — объём держится сниженным до прибыли")

    ea = (ROOT / "ai_scalper_pro" / "AI_Scalper_Pro.mq5").read_text(encoding="utf-8")
    ea_code = re.sub(r"//.*", "", ea)
    check("if(PauseMinutesAfterLossStreak>0)" in ea_code,
          "В советнике то же правило")


def test_no_pauses_left() -> None:
    """Владелец: «Без паузы, убери все».

    Проверяем каждое место, где бот раньше ЖДАЛ, а не отказывал по существу
    сделки. Значения читаем из шаблона заново: тесты выше подменяют настройки
    в памяти, и включённая пауза в поставляемом конфиге прошла бы незамеченной."""
    print("\n[Пауз не осталось ни одной]")
    from state import SymbolState

    fresh = types.ModuleType("fresh_pauses")
    exec((APP / "config.py.example").read_text(encoding="utf-8"), fresh.__dict__)

    check(int(fresh.PAUSE_MINUTES_AFTER_LOSS_STREAK) == 0,
          "Пауза после серии убытков", str(fresh.PAUSE_MINUTES_AFTER_LOSS_STREAK))
    check(int(fresh.MIN_BARS_BETWEEN_REVERSAL) == 0,
          "Ожидание перед разворотом", str(fresh.MIN_BARS_BETWEEN_REVERSAL))
    check(fresh.USE_ROLLOVER_GUARD is False,
          "Пауза вокруг полуночи брокера", str(fresh.USE_ROLLOVER_GUARD))
    check(int(fresh.NEWS_HARD_BLOCK_WINDOW_MIN) == 0,
          "Пауза рядом с важной новостью", str(fresh.NEWS_HARD_BLOCK_WINDOW_MIN))
    check(fresh.USE_TRADING_HOURS is False,
          "Ограничение часов торговли", str(fresh.USE_TRADING_HOURS))

    # А теперь то же самое через сам код, а не через значения настроек
    saved = {name: getattr(CFG, name, None) for name in
             ("MIN_BARS_BETWEEN_REVERSAL", "USE_ROLLOVER_GUARD",
              "ROLLOVER_GUARD_MINUTES", "USE_TRADING_HOURS",
              "USE_NEWS_FILTER")}
    CFG.MIN_BARS_BETWEEN_REVERSAL = 0
    CFG.USE_ROLLOVER_GUARD = False
    CFG.USE_TRADING_HOURS = False
    try:
        sym = SymbolState("XAUUSD")
        sym.last_close_direction = 1     # только что закрыли покупку
        sym.last_close_bar_index = 100
        sym.bar_counter = 100            # тот же самый бар
        check(rm.reversal_cooldown_ok(sym, -1) is True,
              "Разворот возможен сразу, на том же баре")

        check(rm.rollover_guard_ok(False) is True,
              "Полночь брокера торговлю не останавливает")
        check(rm.trading_hours_ok() is True, "Часы торговли не ограничены")

        # Проверяем именно ту минуту, в которую пауза срабатывала бы. Через
        # rollover_guard_ok это выпало бы на реальные часы и проверялось раз в
        # сутки по случайности — то есть не проверялось бы вовсе.
        check(rm.rollover_blocked(0, 0, 0) is False,
              "0 минут роллoвера = паузы нет даже ровно в полночь")
        check(rm.rollover_blocked(5, 0, 0) is False, "И рядом с ней тоже")
        # А если паузу вернуть — она обязана работать как раньше
        check(rm.rollover_blocked(0, 0, 15) is True,
              "Включённая пауза ловит саму минуту смены дня")
        check(rm.rollover_blocked(10, 0, 15) is True, "И 10 минут после")
        check(rm.rollover_blocked(1435, 0, 15) is True,
              "И 5 минут до, через полночь")
        check(rm.rollover_blocked(60, 0, 15) is False,
              "Через час после — уже не пауза")
    finally:
        for name, value in saved.items():
            if value is not None:
                setattr(CFG, name, value)


def test_news_no_longer_pauses() -> None:
    """Новостная пауза снята, но мягкий штраф — не пауза и остаётся."""
    print("\n[Новости больше не останавливают торговлю]")
    import news_calendar as nc
    import trading_schedule as tsched

    saved_filter = getattr(CFG, "USE_NEWS_FILTER", True)
    saved_events = nc._get_events
    # Важная новость ПРЯМО СЕЙЧАС: без неё проверка ничего не проверяет —
    # пустой календарь и так не блокирует.
    from datetime import datetime as dtm
    nc._get_events = lambda: ([{"time": dtm.now(), "currency": "USD",
                                "event": "Nonfarm Payrolls", "impact": "high"}], "")
    CFG.USE_NEWS_FILTER = True
    try:
        check(nc.is_high_impact_event_near("XAUUSD", 30) is True,
              "Проверка вообще работает: с окном 30 новость найдена")

        # Самый жёсткий случай: подбор событий ВСЕГДА что-то находит. Если бы
        # нулевое окно опиралось только на сравнение времён, оно зависело бы
        # от микросекунд — то есть не проверялось бы вовсе.
        saved_near = nc._relevant_events_near
        nc._relevant_events_near = lambda *a, **k: [{"impact": "high"}]
        try:
            check(nc.is_high_impact_event_near("XAUUSD", 0) is False,
                  "Нулевое окно не блокирует, даже когда событие найдено")
            check(nc.is_high_impact_event_near("XAUUSD", -5) is False,
                  "Отрицательное окно тоже не блокирует")
            check(nc.is_high_impact_event_near("XAUUSD", 30) is True,
                  "А ненулевое — блокирует, проверка не сломана насовсем")
        finally:
            nc._relevant_events_near = saved_near
    finally:
        CFG.USE_NEWS_FILTER = saved_filter
        nc._get_events = saved_events

    # Расписание не должно рисовать «паузу», которой нет
    saved_hard = getattr(CFG, "NEWS_HARD_BLOCK_WINDOW_MIN", 30)
    saved_soft = getattr(CFG, "NEWS_SOFT_PENALTY_WINDOW_MIN", 30)
    from datetime import datetime as dt
    high = {"time": dt(2026, 8, 3, 15, 30), "currency": "USD",
            "event": "Nonfarm Payrolls", "impact": "high"}
    medium = {"time": dt(2026, 8, 3, 16, 0), "currency": "USD",
              "event": "Индекс PMI", "impact": "medium"}
    try:
        CFG.NEWS_HARD_BLOCK_WINDOW_MIN = 0
        CFG.NEWS_SOFT_PENALTY_WINDOW_MIN = 30
        check(tsched.event_window(high) is None,
              "Важная новость больше не создаёт окно остановки")

        window = tsched.event_window(medium)
        check(window is not None, "Мягкий штраф остался — он не пауза")
        if window:
            start, end, action = window
            check(action == tsched.ACTION_PENALTY,
                  "И это именно штраф к оценке, а не блокировка", str(action))
            check((end - start).total_seconds() == 60 * 60,
                  "Окно штрафа берётся из своей настройки, а не из снятой",
                  str(end - start))

        # Штраф можно убрать отдельно, не трогая блокировку
        CFG.NEWS_SOFT_PENALTY_WINDOW_MIN = 0
        check(tsched.event_window(medium) is None,
              "Ноль в окне штрафа выключает и его")
    finally:
        CFG.NEWS_HARD_BLOCK_WINDOW_MIN = saved_hard
        CFG.NEWS_SOFT_PENALTY_WINDOW_MIN = saved_soft

    # Штраф и блокировка должны читать РАЗНЫЕ настройки: иначе снятие паузы
    # заодно выключило бы штраф, а это разные вещи.
    engine = code_only((APP / "signal_engine.py").read_text(encoding="utf-8"))
    penalty = engine.split("soft_news_penalty", 1)[0][-400:]
    check("NEWS_SOFT_PENALTY_WINDOW_MIN" in penalty,
          "Штраф берёт своё окно")
    check("NEWS_HARD_BLOCK_WINDOW_MIN" not in penalty,
          "И не зависит от снятой паузы")


def test_per_trade_guards_remain() -> None:
    """Пауз нет — значит всё держится на проверках КОНКРЕТНОЙ сделки.
    Если исчезнут и они, бот останется вообще без тормозов."""
    print("\n[Вместо пауз — проверки каждой сделки]")
    fresh = types.ModuleType("fresh_guards")
    exec((APP / "config.py.example").read_text(encoding="utf-8"), fresh.__dict__)

    check(fresh.USE_SPREAD_FILTER is True,
          "Фильтр спреда остался — он заменяет паузу роллoвера")
    check(fresh.USE_VOLATILITY_SPIKE_GUARD is True,
          "Защита от скачка волатильности осталась — она заменяет паузу на новостях")
    check(float(fresh.MIN_SL_SPREAD_MULTIPLE) > 0,
          "Стоп не может оказаться внутри спреда")
    check(float(fresh.NEWS_SOFT_PENALTY_WINDOW_MIN) > 0,
          "Рядом с новостью отбор сигнала по-прежнему строже")


def test_advisor_has_no_pauses_either() -> None:
    print("\n[В советнике пауз тоже не осталось]")
    text = (ROOT / "ai_scalper_pro" / "Config.mqh").read_text(encoding="utf-8")
    code = re.sub(r"//.*", "", text)

    def input_value(name: str):
        m = re.search(rf"input\s+\w+\s+{re.escape(name)}\s*=\s*([^;]+);", code)
        return m.group(1).strip() if m else None

    check(input_value("MinBarsBetweenReversal") == "0",
          "Ожидание перед разворотом снято",
          str(input_value("MinBarsBetweenReversal")))
    check(input_value("UseRolloverGuard") == "false",
          "Пауза вокруг полуночи снята", str(input_value("UseRolloverGuard")))
    check(input_value("UseNewsFilter") == "false",
          "Жёсткого блока по новостям нет", str(input_value("UseNewsFilter")))
    check(input_value("UseTimeFilter") == "false",
          "Часы торговли не ограничены", str(input_value("UseTimeFilter")))

    check(input_value("UseSpreadFilter") == "true",
          "Фильтр спреда в советнике остался")
    check(input_value("UseVolatilitySpikeGuard") == "true",
          "Защита от скачка волатильности в советнике осталась")


def test_advisor_defaults_match_program() -> None:
    """Советник и программа должны вести себя одинаково. Если снять остановки
    только в программе, советник на графике продолжит выключаться сам — и
    человек решит, что настройка не сработала."""
    print("\n[Советник настроен так же, как программа]")
    text = (ROOT / "ai_scalper_pro" / "Config.mqh").read_text(encoding="utf-8")
    code = re.sub(r"//.*", "", text)

    def input_value(name: str):
        m = re.search(rf"input\s+\w+\s+{re.escape(name)}\s*=\s*([^;]+);", code)
        return m.group(1).strip() if m else None

    check(input_value("UseDailyLossLimit") == "false",
          "Дневной порог убытка выключен и в советнике",
          str(input_value("UseDailyLossLimit")))
    check(input_value("UseMaxDrawdownLimit") == "false",
          "Лимит просадки выключен и в советнике",
          str(input_value("UseMaxDrawdownLimit")))
    check(input_value("PauseMinutesAfterLossStreak") == "0",
          "Паузы после серии убытков нет и в советнике",
          str(input_value("PauseMinutesAfterLossStreak")))

    # Защита размера сделки в советнике при этом остаётся
    check(input_value("UseLossStreakRiskScaling") == "true",
          "Снижение объёма по серии убытков в советнике осталось")
    total_risk = input_value("MaxTotalRiskPercent")
    check(total_risk is not None and float(total_risk) > 0,
          "Потолок совокупного риска в советнике задан", str(total_risk))


# =====================================================================
# 3. «??????» в календаре
# =====================================================================
def test_calendar_writes_utf8() -> None:
    print("\n[Календарь пишется в UTF-8, а не в ANSI]")
    src = (ROOT / "mql5" / "CalendarExport.mq5").read_text(encoding="utf-8")
    # Убираем комментарии: в них специально описана прежняя ошибка
    code = re.sub(r"//.*", "", src)

    check("FILE_ANSI" not in code,
          "Однобайтовая запись FILE_ANSI убрана — она теряла русские буквы")
    check("CP_UTF8" in code, "Текст переводится в UTF-8 явным образом")
    check("StringToCharArray" in code and "FileWriteArray" in code,
          "Пишутся именно байты UTF-8")
    check("FILE_BIN" in code, "Файл открывается в двоичном режиме")
    # Завершающий нулевой байт сломал бы разбор JSON
    check("StringLen(json)" in code,
          "Завершающий нулевой байт в файл не попадает")
    # Атомарность записи не должна была потеряться при правке
    check("FileMove" in code, "Запись по-прежнему атомарная (через временный файл)")


def test_reader_expects_utf8() -> None:
    print("\n[Программа читает файл в той же кодировке]")
    body = code_only((APP / "news_providers.py").read_text(encoding="utf-8"))
    check('encoding="utf-8-sig"' in body or 'encoding="utf-8"' in body,
          "Чтение календаря — в UTF-8")


def test_broken_encoding_is_reported() -> None:
    print("\n[Испорченные названия распознаются и объясняются]")
    broken = [{"event": "??????"}, {"event": "?????? ??"}, {"event": "????"}]
    good = [{"event": "Nonfarm Payrolls"}, {"event": "Ставка ФРС"},
            {"event": "Индекс PMI"}]
    mixed = [{"event": "??????"}, {"event": "Ставка ФРС"},
             {"event": "Индекс PMI"}, {"event": "Розничные продажи"}]

    check(npv.looks_like_broken_encoding(broken) is True,
          "Строка из одних «?» опознана как испорченная")
    check(npv.looks_like_broken_encoding(good) is False,
          "Нормальные названия не считаются испорченными")
    check(npv.looks_like_broken_encoding(mixed) is False,
          "Одно битое название из четырёх — ещё не повод пугать человека")
    check(npv.looks_like_broken_encoding([]) is False, "Пустой список — не ошибка")
    check(npv.looks_like_broken_encoding(None) is False, "None — не ошибка")
    # Название с вопросительным знаком по делу не должно считаться битым
    check(npv.looks_like_broken_encoding([{"event": "Инфляция?"}]) is False,
          "Знак вопроса внутри осмысленного названия не в счёт")

    hint = npv.BROKEN_ENCODING_HINT
    check("шрифт" not in hint.lower() or "сервис" in hint.lower(),
          "Подсказка не сваливает вину на шрифт")
    check("Перезапустить" in hint or "перезапуст" in hint.lower(),
          "Подсказка говорит, что делать: перезапустить сервис")

    ui = code_only((APP / "desktop_app.py").read_text(encoding="utf-8"))
    check("looks_like_broken_encoding" in ui,
          "Программа действительно показывает эту подсказку")


# =====================================================================
# 4. Журнал сделок в облаке
# =====================================================================
SAMPLE = [
    dict(is_bot=True, symbol="XAUUSD", profit=-0.55, duration_sec=9,
         commission=-0.05, swap=0.0),
    dict(is_bot=True, symbol="XAUUSD", profit=-0.62, duration_sec=11,
         commission=-0.05, swap=0.0),
    dict(is_bot=True, symbol="XAUUSD", profit=-0.48, duration_sec=8,
         commission=-0.05, swap=0.0),
    dict(is_bot=True, symbol="XAUUSD", profit=0.21, duration_sec=140,
         commission=-0.05, swap=0.0),
    dict(is_bot=True, symbol="EURUSD", profit=0.30, duration_sec=600,
         commission=-0.02, swap=0.0),
    dict(is_bot=False, symbol="EURUSD", profit=999.0, duration_sec=10),
]


def test_journal_off_by_default() -> None:
    print("\n[Наружу ничего не уходит без разрешения]")
    check(CFG.JOURNAL_CLOUD_ENABLED is False,
          "Выгрузка выключена по умолчанию")
    ok, reason = cj.ready()
    check(ok is False and reason, "Выключено — сказано прямо", reason)

    old = CFG.JOURNAL_CLOUD_ENABLED
    CFG.JOURNAL_CLOUD_ENABLED = True
    CFG.JOURNAL_REPO = ""
    CFG.UPDATE_REPO = ""
    ok, reason = cj.ready()
    check(ok is False and "репозитор" in reason.lower(),
          "Без репозитория — понятная причина", reason)
    CFG.JOURNAL_REPO = "owner/repo"
    CFG.JOURNAL_TOKEN = ""
    ok, reason = cj.ready()
    check(ok is False and "токен" in reason.lower(),
          "Без токена — понятная причина", reason)
    CFG.JOURNAL_TOKEN = "секрет"
    ok, reason = cj.ready()
    check(ok is True, "С репозиторием и токеном — готово", reason)

    result = cj.upload({}, "01.01.2026 00:00:00") if False else None
    CFG.JOURNAL_CLOUD_ENABLED = old


def test_journal_never_leaks_secrets() -> None:
    print("\n[В облако не уходят пароли, ключи и токены]")
    body = code_only((APP / "cloud_journal.py").read_text(encoding="utf-8"))
    for secret in ("MT5_PASSWORD", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                   "TELEGRAM_API_HASH", "SECURITY_SALT", "NEWS_API_KEYS",
                   "DASHBOARD_PASSWORD"):
        check(secret not in body, f"Журнал не читает {secret}")

    # Собранные файлы не должны содержать ничего, кроме сделок
    text = cj.history_csv(SAMPLE)
    a = cj.analyze(SAMPLE)
    summary = cj.summary_markdown("12345", "Broker-Demo", {}, a, "01.01.2026")
    for secret in ("секрет", "password", "token", "enc:"):
        check(secret.lower() not in text.lower(),
              f"В CSV истории нет «{secret}»")
        check(secret.lower() not in summary.lower(),
              f"В разборе нет «{secret}»")

    # config.py целиком в облако не выкладывается
    check("config.py" not in body.replace("config.py.example", ""),
          "config.py не отправляется")


def test_journal_analysis() -> None:
    print("\n[Разбор сделок считает то, что нужно]")
    a = cj.analyze(SAMPLE)
    check(a["trades"] == 5, "Считаются только сделки бота", str(a["trades"]))
    check(a["wins"] == 2 and a["losses"] == 3, "Плюсы и минусы разделены")
    check(a["win_rate"] == 40.0, "Винрейт", str(a["win_rate"]))
    check(a["net"] == -1.14, "Итог", str(a["net"]))
    check(a["instant_deaths"] == 3,
          "Сделки, умершие за секунды, посчитаны", str(a["instant_deaths"]))
    check(a["median_life_sec"] == 11, "Обычное время жизни сделки",
          str(a["median_life_sec"]))
    check(a["by_symbol"]["XAUUSD"]["net"] == -1.44,
          "Итог по паре", str(a["by_symbol"]["XAUUSD"]["net"]))
    check("EURUSD" in a["by_symbol"] and a["by_symbol"]["EURUSD"]["trades"] == 1,
          "Ручная сделка в разбор пары не попала")

    joined = " ".join(a["findings"])
    check("быстрее минуты" in joined,
          "Сказано про сделки, умирающие за секунды")
    check("MIN_SL_SPREAD_MULTIPLE" in joined,
          "Названа настройка, которую надо крутить")
    check("TP_TIGHTEN_MIN_R" in joined,
          "Названа настройка цели, когда средний минус больше среднего плюса")


def test_journal_analysis_edge_cases() -> None:
    print("\n[Разбор не падает на краях]")
    empty = cj.analyze([])
    check(empty["trades"] == 0 and empty["win_rate"] == 0.0,
          "Пустая история — нули, а не ошибка")
    check(cj.analyze(None)["trades"] == 0, "None — тоже не ошибка")

    only_manual = cj.analyze([dict(is_bot=False, symbol="X", profit=-5.0)])
    check(only_manual["trades"] == 0,
          "Только ручные сделки — боту вменять нечего")

    all_win = cj.analyze([dict(is_bot=True, symbol="X", profit=1.0, duration_sec=300),
                          dict(is_bot=True, symbol="X", profit=2.0, duration_sec=300)])
    check(all_win["avg_loss"] == 0.0 and all_win["payoff"] == 0.0,
          "Без единого минуса деления на ноль не происходит")

    no_life = cj.analyze([dict(is_bot=True, symbol="X", profit=-1.0)])
    check(no_life["median_life_sec"] is None,
          "Без времени жизни поле пустое, а не выдуманное")


def test_journal_plural() -> None:
    print("\n[Числа и слова согласованы]")
    for n, want in ((1, "сделка"), (2, "сделки"), (4, "сделки"), (5, "сделок"),
                    (11, "сделок"), (21, "сделка"), (22, "сделки"),
                    (25, "сделок"), (111, "сделок"), (101, "сделка")):
        check(cj.plural(n, "сделка", "сделки", "сделок") == want,
              f"{n} {want}", cj.plural(n, "сделка", "сделки", "сделок"))


def test_journal_csv_shape() -> None:
    print("\n[Файлы журнала читаются человеком и Excel]")
    text = cj.history_csv(SAMPLE)
    lines = [line for line in text.splitlines() if line.strip()]
    check(len(lines) == len(SAMPLE) + 1, "Строк столько же, сколько сделок + заголовок",
          str(len(lines)))
    check(lines[0].count(";") == len(cj.HISTORY_COLUMNS) - 1,
          "Разделитель ';' — как в trades_log.csv")
    check("Прожила, сек" in lines[0],
          "Время жизни сделки есть в таблице — главная улика по минусам")
    check("бот" in text and "вручную" in text,
          "Видно, где сделка бота, а где ручная")

    a = cj.analyze(SAMPLE)
    summary = cj.summary_markdown("12345", "Broker", {"days": 30}, a, "01.01.2026")
    check(summary.startswith("# "), "Разбор — Markdown, открывается прямо на GitHub")
    check("| Винрейт |" in summary, "В таблице есть винрейт")
    check("XAUUSD" in summary, "В разборе есть разбивка по парам")


def test_journal_masks_account() -> None:
    print("\n[Номер счёта можно скрыть]")
    old = getattr(CFG, "JOURNAL_MASK_ACCOUNT", False)
    CFG.JOURNAL_MASK_ACCOUNT = False
    check(cj.account_label("1234567") == "1234567", "По умолчанию номер как есть")
    CFG.JOURNAL_MASK_ACCOUNT = True
    check(cj.account_label("1234567") == "****4567", "Включили — номер скрыт",
          cj.account_label("1234567"))
    check(cj.account_label("12") == "12", "Короткий номер не ломает маску")
    CFG.JOURNAL_MASK_ACCOUNT = old


def test_journal_errors_are_human() -> None:
    print("\n[Ошибки объясняются словами]")
    import urllib.error
    cases = {
        404: "не найден",
        401: "истёк",
        403: "запис",
    }
    for code, expect in cases.items():
        e = urllib.error.HTTPError("u", code, "msg", None, None)
        text = cj.explain_error(e)
        check(expect.lower() in text.lower(), f"HTTP {code} объяснён", text)
    text = cj.explain_error(urllib.error.URLError("нет сети"))
    check("связи" in text.lower(), "Обрыв сети объяснён", text)


def test_journal_upload_rate_limited() -> None:
    print("\n[Плановая выгрузка не долбит GitHub]")
    body = code_only((APP / "cloud_journal.py").read_text(encoding="utf-8"))
    check("upload_interval_seconds" in body, "Есть интервал между выгрузками")
    check("max(60.0" in body, "Чаще раза в минуту выгружать нельзя")

    # Отметка времени ставится ДО отправки — иначе недоступный GitHub
    # превратился бы в бесконечный поток попыток
    fn = body.split("def upload_if_due", 1)[1].split("\ndef ", 1)[0]
    mark = fn.find('_last_upload["ts"] = time.time()')
    call = fn.find("return upload(")
    check(0 < mark < call,
          "Время попытки отмечается до обращения к сети")


def test_journal_uses_write_token() -> None:
    print("\n[Токен записи отделён от токена обновлений]")
    body = code_only((APP / "cloud_journal.py").read_text(encoding="utf-8"))
    check("JOURNAL_TOKEN" in body, "Свой токен для журнала")
    check("UPDATE_TOKEN" not in body,
          "Токен обновлений (только чтение) не используется для записи")


# =====================================================================
# 5. Секреты, которые шифруются, должны и расшифровываться
# =====================================================================
def test_encrypted_fields_are_decrypted() -> None:
    print("\n[Всё, что шифруется, потом расшифровывается]")
    ui = code_only((APP / "desktop_app.py").read_text(encoding="utf-8"))

    # Какие поля программа шифрует при сохранении
    protected = set(re.findall(
        r'_write_config_value\(\s*"([A-Z_]+)"\s*,\s*repr\(\s*(?:secure_store\.)?protect',
        ui))
    # ...и те, что шифруются через локальную обёртку protect(...)
    protected |= set(re.findall(
        r'_write_config_value\(\s*"([A-Z_]+)"\s*,\s*repr\(protect\(', ui))
    protected |= {"UPDATE_TOKEN", "JOURNAL_TOKEN", "TELEGRAM_API_HASH"}

    for field in sorted(protected):
        check(field in ss._SECRET_STR_FIELDS,
              f"{field} расшифровывается при загрузке")

    # Проверка на настоящем шифровании: поле, которого нет в списке,
    # осталось бы строкой "enc:..." и ушло бы в таком виде наружу
    salt = "00" * 16
    encrypted = ss.encrypt_value("настоящий-токен", "пароль", salt)
    module = types.ModuleType("probe")
    module.SECURITY_SALT = salt
    for field in ss._SECRET_STR_FIELDS:
        setattr(module, field, encrypted)
    ss.unlock_config(module, "пароль")
    for field in ss._SECRET_STR_FIELDS:
        value = getattr(module, field)
        check(value == "настоящий-токен",
              f"{field} после загрузки — настоящее значение", repr(value)[:40])


# =====================================================================
# 6. Справка по параметрам: описан каждый, документация не отстаёт
# =====================================================================
def _advanced_params():
    """ADVANCED_PARAMS и CONFIG_SECTIONS из desktop_app.py без импорта
    tkinter и MetaTrader5."""
    tree = ast.parse((APP / "desktop_app.py").read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("ADVANCED_PARAMS", "CONFIG_SECTIONS"):
                found[name] = ast.literal_eval(node.value)
    return found["ADVANCED_PARAMS"], found["CONFIG_SECTIONS"]


def test_every_param_has_help() -> None:
    print("\n[У каждого параметра есть описание]")
    import param_help as ph
    params, sections = _advanced_params()

    no_help = [key for key, *_ in params if not ph.has_help(key)]
    check(not no_help, "Все параметры интерфейса описаны", ", ".join(no_help[:5]))

    # Описание должно существовать в config.py.example, иначе значение по
    # умолчанию в справке будет пустым, а поле в программе — пустым тоже
    no_default = [key for key, ptype, *_ in params
                  if ptype != "secret" and ph.default_of(key) is None]
    check(not no_default, "У каждого параметра есть значение по умолчанию",
          ", ".join(no_default[:5]))

    # Справка не должна ссылаться на параметры, которых уже нет
    known = {key for key, *_ in params}
    stale = [key for key in ph.HELP if key not in known and not hasattr(CFG, key)]
    check(not stale, "В справке нет описаний исчезнувших параметров",
          ", ".join(stale[:5]))

    # Каждая группа параметров должна попасть на какую-то вкладку
    groups = {group for _, _, group, *_ in params}
    placed = {g for _, gs in sections for g in gs}
    check(groups <= placed, "Каждая группа лежит на своей вкладке",
          ", ".join(sorted(groups - placed)))


def test_params_doc_matches_program() -> None:
    print("\n[Документация по параметрам не отстала от программы]")
    doc_path = ROOT / "docs" / "ПАРАМЕТРЫ.md"
    check(doc_path.exists(), "Файл docs/ПАРАМЕТРЫ.md на месте")
    if not doc_path.exists():
        return
    doc = doc_path.read_text(encoding="utf-8")
    params, sections = _advanced_params()

    missing = [key for key, *_ in params if f"`{key}`" not in doc]
    check(not missing, "Каждый параметр описан в документации",
          ", ".join(missing[:5]))

    for name, _ in sections:
        check(f"# Вкладка «{name}»" in doc,
              f"В документации есть раздел вкладки «{name}»")


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ: НАСТРОЙКИ, ПОРОГ УБЫТКА, КАЛЕНДАРЬ, ЖУРНАЛ В ОБЛАКЕ")
    print("=" * 62)

    test_config_migrate_adds_missing()
    test_config_migrate_never_overwrites()
    test_config_migrate_clears_stale_update_branch()
    test_config_migrate_defaults_update_repo()
    test_config_migrate_widens_stale_stop_loss()
    test_config_migrate_does_not_touch_customized_stop_loss()
    test_config_migrate_fresh_example_needs_no_stop_loss_migration()
    test_config_migrate_skips_multiline()
    test_ui_falls_back_to_default()
    test_migration_runs_on_start()

    test_daily_loss_limit_off()
    test_zero_means_no_limit()
    test_nothing_halts_trading()
    test_trade_risk_cap_blocks_too_expensive_symbol()
    test_trade_risk_cap_reason_reaches_interface()
    test_risk_is_capped_per_trade()
    test_loss_counter_not_reset_without_pause()
    test_no_pauses_left()
    test_news_no_longer_pauses()
    test_per_trade_guards_remain()
    test_advisor_has_no_pauses_either()
    test_advisor_defaults_match_program()

    test_calendar_writes_utf8()
    test_reader_expects_utf8()
    test_broken_encoding_is_reported()

    test_journal_off_by_default()
    test_journal_never_leaks_secrets()
    test_journal_analysis()
    test_journal_analysis_edge_cases()
    test_journal_plural()
    test_journal_csv_shape()
    test_journal_masks_account()
    test_journal_errors_are_human()
    test_journal_upload_rate_limited()
    test_journal_uses_write_token()

    test_encrypted_fields_are_decrypted()

    test_every_param_has_help()
    test_params_doc_matches_program()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
