#!/usr/bin/env python3
"""Тесты приёма настроек торговли из GitHub.

ОТКУДА ЗАДАЧА. Владелец: «сделай, чтобы он сам загружался на GitHub, чтобы ты
мог проверить в любой момент и изменить настройки торговли, и настройки сами
загрузились без всяких нажатий, чтобы программа сама проверяла обновления и
загружала нововведения».

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ В ПЕРВУЮ ОЧЕРЕДЬ — не «настройка применилась», а
ГРАНИЦЫ. Файл из интернета управляет реальными деньгами на реальном счёте.
Опасны не опечатки, а три вещи:

  1. Подмена ИСТОЧНИКА ОБНОВЛЕНИЙ. Разреши менять UPDATE_REPO удалённо — и
     тот, кто получил доступ к репозиторию, подменит не настройку, а всю
     программу целиком. Это уже не про торговлю, это про машину владельца.
  2. Утечка или подмена секретов: токенов, паролей, логинов, путей.
  3. Дикие значения: «риск 50%» вместо «0.5%» доедет до счёта за минуты.

Плюс отдельно: включить торговлю удалённо нельзя, выключить можно.

Запуск:  python3 tests/test_remote_settings.py
"""

from __future__ import annotations

import ast
import json
import os
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


cfg = types.ModuleType("config")
cfg.__file__ = str(APP / "config.py.example")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg
CFG = cfg


class _FakeMT5(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return name


sys.modules["MetaTrader5"] = _FakeMT5("MetaTrader5")

import remote_settings as rs      # noqa: E402


# =====================================================================
# ГРАНИЦЫ — самое важное
# =====================================================================
def test_update_source_can_never_be_changed() -> None:
    """САМАЯ ОПАСНАЯ ДЫРА, будь она открыта. Источник обновлений — это ответ
    на вопрос «чьему коду мы доверяем». Позволить менять его файлом из
    интернета значит отдать машину."""
    print("\n[Источник обновлений менять нельзя ни при каких условиях]")
    for name in ("UPDATE_REPO", "UPDATE_BRANCH", "UPDATE_ENABLED", "UPDATE_TOKEN"):
        accepted, rejected = rs.validate({name: "злоумышленник/его-репозиторий"})
        check(name not in accepted, f"{name} не применяется")
        check(any(name in r for r in rejected), f"И отказ объяснён: {name}",
              str(rejected))
        check(name in rs.FORBIDDEN, f"{name} назван в списке запрещённых явно")
        check(name not in rs.ALLOWED, f"{name} отсутствует в разрешённых")


def test_secrets_and_paths_are_forbidden() -> None:
    print("\n[Токены, пароли, логины и пути]")
    dangerous = {
        "JOURNAL_TOKEN": "ghp_чужойтокен",
        "DASHBOARD_PASSWORD": "пусто",
        "DASHBOARD_PASSWORD_HASH": "",
        "SECURITY_SALT": "",
        "REQUIRE_LOGIN": False,
        "MT5_LOGIN": 999,
        "MT5_PASSWORD": "чужой",
        "MT5_SERVER": "чужой-сервер",
        "MT5_PATH": "C:\\злой\\terminal64.exe",
        "TELEGRAM_API_HASH": "x",
        "ANTHROPIC_API_KEY": "x",
        "LOG_CSV_PATH": "C:\\куда-то\\чужое.csv",
    }
    accepted, rejected = rs.validate(dangerous)
    check(accepted == {}, "Не применилось НИЧЕГО из опасного", str(accepted))
    check(len(rejected) == len(dangerous), "И на каждое дан отказ",
          f"{len(rejected)} из {len(dangerous)}")

    # Список запрещённых должен полностью исключаться из разрешённых:
    # пересечение означало бы, что запрет только на словах.
    overlap = rs.FORBIDDEN & set(rs.ALLOWED)
    check(not overlap, "Запрещённое и разрешённое нигде не пересекаются",
          str(overlap))

    # ВТОРОЙ СЛОЙ ЗАЩИТЫ. Сегодня запрещённых имён нет в разрешённых, поэтому
    # их отсекает уже белый список — и проверка FORBIDDEN выглядит лишней. Но
    # она нужна не от сегодняшнего файла, а от БУДУЩЕЙ ОШИБКИ: однажды кто-то
    # (в том числе я) добавит UPDATE_REPO в разрешённые «чтобы было удобнее»,
    # и белый список пропустит подмену источника обновлений.
    #
    # Подкладываем опасное имя прямо в разрешённые и проверяем, что запрет
    # всё равно сильнее.
    saved = dict(rs.ALLOWED)
    try:
        rs.ALLOWED["UPDATE_REPO"] = lambda v: (v, "")
        accepted, rejected = rs.validate({"UPDATE_REPO": "злой/репозиторий"})
        check("UPDATE_REPO" not in accepted,
              "Даже попав в разрешённые по ошибке, источник обновлений "
              "остаётся запрещённым", str(accepted))
        check(any("запрещ" in r for r in rejected),
              "И причина названа", str(rejected))
    finally:
        rs.ALLOWED.clear()
        rs.ALLOWED.update(saved)


def test_unknown_settings_are_rejected() -> None:
    """Белый список, а не чёрный: неизвестное отклоняется само собой. Чёрный
    список пришлось бы дополнять на каждую новую настройку, и однажды про
    неё забыли бы."""
    print("\n[Всё неизвестное отклоняется]")
    accepted, rejected = rs.validate({"КАКАЯ_ТО_НОВАЯ_НАСТРОЙКА": 1,
                                      "__builtins__": {}, "os": "x"})
    check(accepted == {}, "Неизвестные имена не применяются")
    check(len(rejected) == 3, "На каждое дан отказ", str(rejected))


def test_values_are_range_checked() -> None:
    """Опечатка в числе — самая вероятная ошибка, и она доедет до счёта."""
    print("\n[Дикие значения не проходят]")
    bad = [
        ("MAX_TRADE_RISK_PERCENT_OF_EQUITY", 50, "риск 50% вместо 0.5%"),
        ("MAX_TRADE_RISK_PERCENT_OF_EQUITY", -1, "отрицательный риск"),
        ("R_TRAIL_GIVEBACK_R", 100, "отступ в 100 R"),
        ("THIN_SPREAD_RATIO", 1000, "порог в 1000 раз"),
        ("MAX_SIMULTANEOUS_POSITIONS", 10000, "десять тысяч сделок сразу"),
        ("MAX_SIMULTANEOUS_POSITIONS", 2.5, "дробное число сделок"),
        ("TP_TIGHTEN_MIN_R", 0.1, "цель меньше половины риска"),
        ("USE_R_TRAIL_LADDER", "да", "текст вместо да/нет"),
        ("USE_R_TRAIL_LADDER", 1, "единица вместо true"),
        ("NEWS_TRADE_MIN_IMPACT", "любой", "несуществующая важность"),
    ]
    for name, value, why in bad:
        accepted, rejected = rs.validate({name: value})
        check(accepted == {}, f"Отклонено: {why}", str(accepted))
        check(rejected and name in rejected[0], f"И названа причина: {why}",
              str(rejected))

    good = [
        ("MAX_TRADE_RISK_PERCENT_OF_EQUITY", 1.5),
        ("R_TRAIL_GIVEBACK_R", 0.4),
        ("MAX_SIMULTANEOUS_POSITIONS", 4),
        ("USE_R_TRAIL_LADDER", False),
        ("NEWS_TRADE_MIN_IMPACT", "high"),
    ]
    for name, value in good:
        accepted, rejected = rs.validate({name: value})
        check(name in accepted, f"Разумное значение проходит: {name}={value}",
              str(rejected))


def test_trading_can_be_stopped_but_not_started() -> None:
    """Остановка — защита, её нужно иметь под рукой. Запуск реальной торговли
    человек делает сам, у своего компьютера."""
    print("\n[Торговлю можно выключить удалённо, но не включить]")
    accepted, _ = rs.validate({"LIVE_TRADING": False})
    check(accepted.get("LIVE_TRADING") is False, "Выключить можно")

    accepted, rejected = rs.validate({"LIVE_TRADING": True})
    check("LIVE_TRADING" not in accepted, "Включить нельзя")
    check(any("нельзя" in r for r in rejected), "И объяснено почему",
          str(rejected))


def test_ladder_shape_is_checked() -> None:
    print("\n[Лестница трейлинга проверяется по смыслу]")
    ok, _ = rs.validate({"R_TRAIL_LADDER": [[0.3, 0.0], [1.0, 0.45]]})
    check("R_TRAIL_LADDER" in ok, "Правильная лестница проходит")

    # Запирать больше порога нельзя: стоп оказался бы ВПЕРЕДИ цены.
    bad, rejected = rs.validate({"R_TRAIL_LADDER": [[1.0, 2.0]]})
    check(bad == {}, "Ступень, запирающая больше порога, отклонена")
    check(rejected and "не меньше порога" in rejected[0], "И сказано почему",
          str(rejected))

    for value, why in (([], "пустая лестница"),
                       ([[1.0]], "ступень из одного числа"),
                       ([[1.0, 0.5, 0.2]], "три числа в ступени"),
                       ("не список", "текст вместо списка"),
                       ([[0, 0]], "нулевой порог")):
        got, _ = rs.validate({"R_TRAIL_LADDER": value})
        check(got == {}, f"Отклонено: {why}")


def test_symbols_are_checked() -> None:
    print("\n[Список пар]")
    ok, _ = rs.validate({"SYMBOLS": ["eurusd", " gbpusd "]})
    check(ok.get("SYMBOLS") == ["EURUSD", "GBPUSD"],
          "Имена приводятся к верхнему регистру и обрезаются", str(ok))

    for value, why in (("EURUSD", "строка вместо списка"),
                       ([""], "пустое имя"),
                       ([123], "число вместо имени"),
                       (["A" * 30], "слишком длинное имя"),
                       (["EUR USD; rm -rf"], "посторонние знаки")):
        got, _ = rs.validate({"SYMBOLS": value})
        check(got == {}, f"Отклонено: {why}", str(got))


def test_no_code_execution_anywhere() -> None:
    """Файл из интернета не должен уметь принести с собой поведение."""
    print("\n[Никакого выполнения кода]")
    body = (APP / "remote_settings.py").read_text(encoding="utf-8")
    tree = ast.parse(body)
    dangerous = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ("eval", "exec", "compile", "__import__"):
                dangerous.append(f"{node.func.id} (строка {node.lineno})")
    check(not dangerous, "Ни eval, ни exec, ни compile", "; ".join(dangerous))
    check("json.loads" in body, "Читается именно JSON, а не код Python")
    # literal_eval безопасен (только литералы) и нужен для чтения отметки
    check("ast.literal_eval" in body or "literal_eval" in body,
          "Отметка о применении читается безопасным разбором литерала")


# =====================================================================
# ПРИМЕНЕНИЕ
# =====================================================================
def _tmp_config(body: str, d: str) -> str:
    path = os.path.join(d, "config.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def test_apply_writes_and_marks() -> None:
    print("\n[Применение записывает настройки и ставит отметку]")
    with tempfile.TemporaryDirectory() as d:
        path = _tmp_config("R_TRAIL_GIVEBACK_R = 0.5\nSYMBOLS = ['EURUSD']\n", d)
        data = {"id": "проба-1", "settings": {"R_TRAIL_GIVEBACK_R": 0.35,
                                              "MAX_SIMULTANEOUS_POSITIONS": 4}}
        report = rs.apply(data, path)
        check(report["changed"] is True, "Правка применена", str(report))

        module = types.ModuleType("m")
        exec(open(path, encoding="utf-8").read(), module.__dict__)
        check(module.R_TRAIL_GIVEBACK_R == 0.35, "Значение записано",
              str(module.R_TRAIL_GIVEBACK_R))
        check(module.MAX_SIMULTANEOUS_POSITIONS == 4, "И второе тоже")
        check(module.SYMBOLS == ["EURUSD"], "Чужие настройки не тронуты")
        check(getattr(module, rs.MARKER, "") == "проба-1", "Отметка поставлена")

        # Повтор той же правки ничего не делает: иначе программа переписывала
        # бы config.py каждые несколько минут, а торговый цикл перечитывал бы
        # его без нужды.
        report2 = rs.apply(data, path)
        check(report2["changed"] is False, "Та же правка второй раз не применяется")
        check("уже применена" in report2["note"], "И это сказано", report2["note"])

        # Новый id — применяется снова
        data2 = {"id": "проба-2", "settings": {"R_TRAIL_GIVEBACK_R": 0.6}}
        check(rs.apply(data2, path)["changed"] is True,
              "Новый id — новая правка применяется")


def test_apply_needs_an_id() -> None:
    print("\n[Без id правка не применяется]")
    with tempfile.TemporaryDirectory() as d:
        path = _tmp_config("R_TRAIL_GIVEBACK_R = 0.5\n", d)
        report = rs.apply({"settings": {"R_TRAIL_GIVEBACK_R": 0.1}}, path)
        check(report["changed"] is False, "Без id не применяется")
        check("id" in report["note"], "И объяснено почему", report["note"])


def test_bad_settings_do_not_break_config() -> None:
    """Даже полностью негодная правка не должна испортить config.py."""
    print("\n[Негодная правка не ломает настройки]")
    with tempfile.TemporaryDirectory() as d:
        original = "R_TRAIL_GIVEBACK_R = 0.5\nUPDATE_REPO = 'simafon1-cyber/repp'\n"
        path = _tmp_config(original, d)
        report = rs.apply({"id": "плохая",
                           "settings": {"UPDATE_REPO": "злой/репозиторий",
                                        "MAX_TRADE_RISK_PERCENT_OF_EQUITY": 99}},
                          path)
        check(report["changed"] is False, "Ничего не применилось")
        module = types.ModuleType("m")
        exec(open(path, encoding="utf-8").read(), module.__dict__)
        check(module.UPDATE_REPO == "simafon1-cyber/repp",
              "Источник обновлений остался прежним", module.UPDATE_REPO)
        check(module.R_TRAIL_GIVEBACK_R == 0.5, "И остальные настройки целы")
        # Отметку ставим даже для негодной правки: иначе программа ругалась бы
        # на неё каждые несколько минут без конца.
        check(getattr(module, rs.MARKER, "") == "плохая",
              "Но отметка поставлена — чтобы не ругаться на неё вечно")


def test_settings_url() -> None:
    print("\n[Откуда берётся файл]")
    saved = getattr(CFG, "REMOTE_SETTINGS_URL", "")
    try:
        CFG.REMOTE_SETTINGS_URL = ""
        url = rs.settings_url()
        check(url.startswith("https://raw.githubusercontent.com/"), "Прямая ссылка", url)
        check(CFG.UPDATE_REPO in url, "Из репозитория обновлений", url)
        check(url.endswith("remote/settings.json"), "На нужный файл", url)

        CFG.REMOTE_SETTINGS_URL = "https://пример/свой.json"
        check(rs.settings_url() == "https://пример/свой.json",
              "Свою ссылку можно задать вручную")
    finally:
        CFG.REMOTE_SETTINGS_URL = saved


def test_file_in_repo_is_valid() -> None:
    """Файл в репозитории должен быть настоящим и проходить проверку —
    иначе программа будет ругаться у всех сразу."""
    print("\n[Файл remote/settings.json в репозитории годен]")
    path = ROOT / "remote" / "settings.json"
    check(path.exists(), "Файл существует")
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    check(bool(str(data.get("id", "")).strip()), "У правки есть id", str(data.get("id")))
    accepted, rejected = rs.validate(data.get("settings", {}))
    check(not rejected, "Все настройки в нём проходят проверку", str(rejected))
    check(bool(accepted), "И хотя бы одна применяется")
    check("comment" in data, "Есть пояснение для человека")


def test_wired_into_trading_loop() -> None:
    print("\n[Подключено к торговому циклу]")
    src = (APP / "main.py").read_text(encoding="utf-8")
    check("import remote_settings" in src, "Модуль подключён")
    check("sync_remote_settings()" in src, "Синхронизация вызывается из цикла")
    # Порядок важен: сначала забрать настройки, потом перечитать config.py,
    # иначе изменения применятся только на следующей итерации.
    loop = src.split("while True:", 1)[1]
    check(loop.index("sync_remote_settings()") < loop.index("reload_config_if_changed"),
          "Настройки забираются ДО перечитывания config.py")

    ui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    check("_schedule_update_check" in ui, "Периодическая проверка обновлений есть")
    check("UPDATE_CHECK_MINUTES" in ui, "И у неё есть настройка")
    # Следующая проверка ставится ДО текущей: иначе одна неудача (нет сети)
    # оборвала бы цепочку навсегда.
    body = ui.split("def _periodic_update_check", 1)[1].split("\n    def ", 1)[0]
    check(body.index("_schedule_update_check") < body.index("check_updates"),
          "Следующая проверка ставится заранее — сбой не обрывает цепочку")


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ: НАСТРОЙКИ ИЗ GITHUB")
    print("=" * 62)

    test_update_source_can_never_be_changed()
    test_secrets_and_paths_are_forbidden()
    test_unknown_settings_are_rejected()
    test_values_are_range_checked()
    test_trading_can_be_stopped_but_not_started()
    test_ladder_shape_is_checked()
    test_symbols_are_checked()
    test_no_code_execution_anywhere()

    test_apply_writes_and_marks()
    test_apply_needs_an_id()
    test_bad_settings_do_not_break_config()
    test_settings_url()
    test_file_in_repo_is_valid()
    test_wired_into_trading_loop()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
