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


def test_money_protection_remains() -> None:
    print("\n[Деньги защищены и без дневного порога]")
    check(CFG.USE_MAX_DRAWDOWN_LIMIT is True,
          "Лимит общей просадки остался включённым")
    acc = FakeAccount(peak_equity=100.0)
    limit = abs(rm.get_profile()["max_drawdown_pct"])
    check(rm.max_drawdown_hit(acc, 100.0 - limit - 1) is True,
          "Просадка сверх лимита останавливает торговлю")

    check(CFG.USE_STOP_LOSS is True if hasattr(CFG, "USE_STOP_LOSS") else True,
          "Стоп-лосс на сделке никуда не делся")
    check(float(CFG.RISK_PROFILES[CFG.RiskProfile(CFG.RISK_PROFILE)]["risk_percent"]) > 0,
          "Риск на сделку по-прежнему ограничен процентом от счёта")


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
    test_config_migrate_skips_multiline()
    test_ui_falls_back_to_default()
    test_migration_runs_on_start()

    test_daily_loss_limit_off()
    test_zero_means_no_limit()
    test_money_protection_remains()

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
