#!/usr/bin/env python3
"""Тесты «всё внутри программы»: встроенный мост, проверка системы,
обновление из GitHub.

Главное, что проверяется:
  1. Мост слушает ТОЛЬКО 127.0.0.1 — наружу не открывается ни при какой
     настройке, адрес прибит в коде.
  2. Ответ моста — ограничитель: risk_multiplier зажат в 0..1, любой
     непонятный ответ превращается в безопасный отказ, а не в догадку.
  3. Мост не тянет fastapi/uvicorn — иначе пользователю пришлось бы их
     ставить отдельно, ради чего всё и затевалось.
  4. Обновление не ставится молча и не смешивает версии файлов.

Запуск:  python3 tests/test_system.py
"""

from __future__ import annotations

import ast
import json
import sys
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
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg

CFG = cfg

import bridge_host as bh      # noqa: E402
import diagnostics as dg      # noqa: E402
import updater as up          # noqa: E402


# =====================================================================
# 1. Мост: безопасность и ограничитель
# =====================================================================
def test_bridge_binds_localhost_only() -> None:
    print("\n[Мост слушает только 127.0.0.1]")

    check(bh.HOST == "127.0.0.1", "Адрес привязки — локальный", bh.HOST)

    src = (APP / "bridge_host.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Адрес не должен браться из настроек: иначе его можно случайно открыть
    # наружу, а мост отвечает торговому роботу.
    assigns = [n for n in tree.body if isinstance(n, ast.Assign)
               and getattr(n.targets[0], "id", "") == "HOST"]
    check(len(assigns) == 1 and isinstance(assigns[0].value, ast.Constant),
          "HOST задан константой, а не настройкой")
    check("BRIDGE_HOST" not in src, "Нет настройки адреса привязки — открыть наружу нельзя")
    for bad in ("0.0.0.0", '""', "'',"):
        check(f'ThreadingHTTPServer(("{bad}"' not in src, f"Не слушает {bad}")


def test_bridge_no_heavy_deps() -> None:
    print("\n[Мост без лишних зависимостей]")

    src = (APP / "bridge_host.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    for heavy in ("fastapi", "uvicorn", "flask", "starlette"):
        check(heavy not in imported,
              f"Не требует {heavy} — иначе пользователю пришлось бы ставить его отдельно")
    check("http" in imported, "Использует встроенный http.server")


def test_bridge_validation() -> None:
    print("\n[Ответ моста — ограничитель, а не команда]")

    good = {"regime": "trend", "trade_allowed": True,
            "risk_multiplier": 0.5, "reason": "спокойно"}
    out = bh.validate_response(good)
    check(out["risk_multiplier"] == 0.5, "Нормальный ответ проходит")
    check(out["regime"] == "trend", "Режим сохранён")

    # ГЛАВНОЕ: множитель может только УМЕНЬШАТЬ объём
    out = bh.validate_response({**good, "risk_multiplier": 5.0})
    check(out["risk_multiplier"] == 1.0,
          "Множитель больше 1 зажимается — увеличить риск нельзя", str(out["risk_multiplier"]))
    out = bh.validate_response({**good, "risk_multiplier": -3})
    check(out["risk_multiplier"] == 0.0, "Отрицательный множитель становится нулём")

    # Запрет торговли обязан обнулять множитель, иначе получится
    # "торговать нельзя, но объём 0.8"
    out = bh.validate_response({**good, "trade_allowed": False})
    check(out["risk_multiplier"] == 0.0 and out["trade_allowed"] is False,
          "Запрет торговли обнуляет множитель", str(out))

    # Мусор -> безопасный отказ, а не попытка угадать
    for bad in (None, "текст", [], 42,
                {"regime": "выдуманный", "trade_allowed": True, "risk_multiplier": 1},
                {"regime": "trend", "trade_allowed": "да", "risk_multiplier": 1},
                {"regime": "trend", "trade_allowed": True, "risk_multiplier": "много"},
                {"regime": "trend", "trade_allowed": True},
                {}):
        out = bh.validate_response(bad)
        check(out["regime"] == "chaos" and out["trade_allowed"] is False
              and out["risk_multiplier"] == 0.0,
              f"Непонятный ответ -> безопасный отказ: {str(bad)[:40]}", str(out))

    out = bh.validate_response({**good, "risk_multiplier": float("nan")})
    check(out["regime"] == "chaos", "NaN тоже отвергается")

    # Слишком длинная причина обрезается, а не уезжает в советник целиком
    out = bh.validate_response({**good, "reason": "x" * 5000})
    check(len(out["reason"]) <= 300, "Длинная причина обрезается", str(len(out["reason"])))


def test_bridge_cache() -> None:
    print("\n[Кэш моста — отдельный на каждый инструмент]")

    from datetime import datetime, timedelta

    bh.clear_cache()
    calls = []

    saved = bh._ask_model
    bh._ask_model = lambda symbol: (calls.append(symbol) or {
        "regime": "trend", "trade_allowed": True,
        "risk_multiplier": 0.7, "reason": symbol})
    try:
        now = datetime(2026, 8, 3, 12, 0, 0)
        bh.regime_for("EURUSD", now)
        bh.regime_for("EURUSD", now + timedelta(minutes=1))
        check(calls == ["EURUSD"], "Повторный запрос берётся из кэша", str(calls))

        bh.regime_for("XAUUSD", now)
        check(calls == ["EURUSD", "XAUUSD"],
              "У золота свой кэш — чужой ответ не подставляется", str(calls))

        CFG.BRIDGE_CACHE_TTL_MIN = 45
        bh.regime_for("EURUSD", now + timedelta(minutes=46))
        check(calls.count("EURUSD") == 2, "После TTL спрашиваем заново", str(calls))

        check(bh.regime_for("", now)["regime"] == "chaos",
              "Пустой символ — безопасный отказ")
    finally:
        bh._ask_model = saved
        bh.clear_cache()


def test_bridge_endpoints() -> None:
    print("\n[Эндпоинты моста]")

    src = (APP / "bridge_host.py").read_text(encoding="utf-8")
    check('"/health"' in src, "Есть /health")
    check('"/regime"' in src, "Есть /regime")
    check('"symbol"' in src, "Символ читается из запроса")
    # Неизвестный адрес не должен молча отдавать 200
    check("404" in src, "Неизвестный адрес -> 404")
    check("400" in src, "Запрос без символа -> 400")

    # Ответ моста должен быть валидным JSON
    payload = json.dumps(bh.CHAOS, ensure_ascii=False)
    check(json.loads(payload)["trade_allowed"] is False,
          "Безопасный ответ сериализуется и запрещает торговлю")


# =====================================================================
# 2. Проверка системы
# =====================================================================
def test_diagnostics() -> None:
    print("\n[Проверка компьютера]")

    results = dg.run_all()
    check(len(results) > 5, f"Проверок достаточно: {len(results)}")

    for r in results:
        check(set(r) == {"name", "level", "detail", "fix"},
              f"У проверки «{r.get('name')}» правильный набор полей", str(sorted(r)))
        check(r["level"] in (dg.OK, dg.WARN, dg.FAIL),
              f"У «{r['name']}» понятное состояние", r["level"])

    names = {r["name"] for r in results}
    for expected in ("Python", "MetaTrader 5", "MetaEditor", "MetaTrader5",
                     "telethon", "Свободное место"):
        check(expected in names, f"Проверяется: {expected}", str(sorted(names))[:120])

    # У всего, что не в порядке, должна быть подсказка что делать
    for r in results:
        if r["level"] == dg.FAIL:
            check(bool(r["fix"]), f"У проблемы «{r['name']}» есть подсказка", str(r))

    check(isinstance(dg.summary(results), str) and dg.summary(results),
          "Есть итоговая строка", dg.summary(results))

    # Итог зависит от состояния, а не выдуман
    fake_fail = [{"name": "x", "level": dg.FAIL, "detail": "", "fix": ""}]
    check("работать не будет" in dg.summary(fake_fail), "Про отказ сказано прямо")
    check(dg.has_blocking_problems(fake_fail) is True, "Отказ распознаётся")
    fake_ok = [{"name": "x", "level": dg.OK, "detail": "", "fix": ""}]
    check(dg.has_blocking_problems(fake_ok) is False, "Успех не считается отказом")
    check("готова" in dg.summary(fake_ok), "Про готовность сказано прямо")


# =====================================================================
# 3. Обновление
# =====================================================================
def test_updater_rules() -> None:
    print("\n[Обновление из GitHub]")

    CFG.UPDATE_ENABLED = False
    result = up.check()
    check(result["available"] is False and result["error"],
          "Выключено — честно сообщает", str(result))

    CFG.UPDATE_ENABLED = True
    CFG.UPDATE_REPO = ""
    result = up.check()
    check("репозитор" in result["error"].lower(), "Без репозитория — понятная ошибка",
          result["error"])

    CFG.UPDATE_REPO = "без-косой-черты"
    check(up.check()["error"], "Неверный формат репозитория отвергается")

    CFG.UPDATE_REPO = "owner/repo"
    check(up.repo() == "owner/repo", "Репозиторий читается из настроек")
    check(up.branch() == "main", "Ветка по умолчанию — main")

    # Ошибки сети переводятся на русский
    import urllib.error
    msg = up.explain_error(urllib.error.HTTPError("u", 404, "nf", None, None))
    check("не найден" in msg.lower(), "404 объяснён", msg)
    msg = up.explain_error(urllib.error.HTTPError("u", 403, "no", None, None))
    check("токен" in msg.lower(), "403 подсказывает про токен", msg)
    msg = up.explain_error(urllib.error.URLError("нет сети"))
    check("связи" in msg.lower(), "Нет сети — понятно", msg)


def test_updater_does_not_mix_versions() -> None:
    print("\n[Обновление не смешивает версии]")

    saved = up.download_text
    calls = []

    def half_broken(path):
        calls.append(path)
        if len(calls) > 3:
            raise RuntimeError("сеть пропала")
        return "// файл"

    up.download_text = half_broken
    try:
        report = up.update_advisors()
        check(report["errors"], "Частичная закачка — это ошибка")
        check(any("смешать" in e or "отменена" in e for e in report["errors"]),
              "Установка отменяется, чтобы не смешать старые и новые файлы",
              str(report["errors"]))
        check(report["installed"] == "", "Ничего не установлено")
    finally:
        up.download_text = saved


def test_updater_never_silent() -> None:
    print("\n[Обновление не ставится молча]")

    gui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    tree = ast.parse(gui)

    after = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_after_update_check":
            after = ast.get_source_segment(gui, node)
    check(after is not None, "Обработчик проверки найден")
    if after:
        check("askyesno" in after,
              "Спрашивает согласие ДАЖЕ при автоматической проверке — подменять "
              "торгового робота без ведома человека нельзя")

    check("check_updates(silent=True)" in gui, "При запуске проверка тихая")
    check("UPDATE_CHECK_ON_START" in gui, "Автопроверку можно выключить")

    # Токен GitHub — секрет, шифруется как ключи API
    saver = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "save_system_settings":
            saver = ast.get_source_segment(gui, node)
    check(saver is not None and "protect_secret" in saver,
          "Токен GitHub шифруется наравне с ключами API")

    src = (APP / "updater.py").read_text(encoding="utf-8")
    check("UPDATE_TOKEN" in (APP / "config.py.example").read_text(encoding="utf-8"),
          "Настройка токена есть в шаблоне конфига")
    check("Bearer" in src, "Токен передаётся заголовком, а не в адресе")


def test_bundled_everything() -> None:
    print("\n[Всё едет внутри программы]")

    wf = (ROOT / ".github" / "workflows" / "build-exe.yml").read_text(encoding="utf-8")
    for mod in ("bridge_host", "diagnostics", "updater"):
        check(f"--hidden-import {mod}" in wf, f"{mod} виден сборщику")

    # Мост не должен требовать отдельной установки
    req = (APP / "requirements.txt").read_text(encoding="utf-8").lower()
    check("fastapi" not in req and "uvicorn" not in req,
          "fastapi/uvicorn не нужны основной программе — мост на встроенном сервере")

    gui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    check('"Система": tab_system' in gui, "Вкладка «Система» зарегистрирована")
    check("_start_bridge_if_enabled" in gui, "Мост поднимается сам при запуске")


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ СИСТЕМЫ: МОСТ, ПРОВЕРКИ, ОБНОВЛЕНИЕ")
    print("=" * 62)

    test_bridge_binds_localhost_only()
    test_bridge_no_heavy_deps()
    test_bridge_validation()
    test_bridge_cache()
    test_bridge_endpoints()
    test_diagnostics()
    test_updater_rules()
    test_updater_does_not_mix_versions()
    test_updater_never_silent()
    test_bundled_everything()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
