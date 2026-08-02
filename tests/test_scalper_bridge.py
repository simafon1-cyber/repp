#!/usr/bin/env python3
"""Тесты моста AI Scalper Pro: разбор символов, чтение ключа, endpoints.

Запуск:  python3 tests/test_scalper_bridge.py
Ключ API не нужен — обращений к Twelve Data здесь нет.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
BRIDGE = BASE.parent / "ai_scalper_pro" / "bridge" / "bridge_example.py"

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


def load_bridge():
    spec = importlib.util.spec_from_file_location("scalper_bridge", BRIDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_symbols(bridge) -> None:
    print("\n=== 1. Разбор символов с суффиксами брокера ===")
    to_td = bridge.to_twelvedata_symbol
    cases = [
        ("EURUSD", "EUR/USD"),
        ("XAUUSD", "XAU/USD"),
        ("EURUSDm", "EUR/USD"),
        ("EURUSDc", "EUR/USD"),
        ("XAUUSDz", "XAU/USD"),
        ("XAUUSD.raw", "XAU/USD"),
        ("EURUSD.a", "EUR/USD"),
        ("GBPUSD_i", "GBP/USD"),
        ("BTCUSD", "BTC/USD"),
        # Не должны ломаться: суффикса нет, обрезать нечего
        ("PLATINUM", "PLATINUM"),
        ("US30", "US30"),
        ("GOLD", "GOLD"),
    ]
    for src, expected in cases:
        got = to_td(src)
        check(got == expected, f"{src} -> {expected}", f"получено {got}")


def test_env_reading() -> None:
    print("\n=== 2. Чтение ключа API из файла .env ===")
    bridge_dir = BRIDGE.parent
    env_path = bridge_dir / ".env"
    had_env = env_path.exists()
    backup = env_path.read_text(encoding="utf-8") if had_env else None
    saved = os.environ.pop("TWELVEDATA_API_KEY", None)

    try:
        # Ключ в .env подхватывается
        env_path.write_text("TWELVEDATA_API_KEY=ключ-из-файла\n", encoding="utf-8")
        bridge = load_bridge()
        check(bridge.API_KEY == "ключ-из-файла", "ключ читается из .env", bridge.API_KEY)

        # Кавычки вокруг значения не должны попадать в сам ключ
        os.environ.pop("TWELVEDATA_API_KEY", None)
        env_path.write_text('TWELVEDATA_API_KEY="ключ-в-кавычках"\n', encoding="utf-8")
        bridge = load_bridge()
        check(bridge.API_KEY == "ключ-в-кавычках", "кавычки убираются", bridge.API_KEY)

        # Комментарии и пустые строки игнорируются
        os.environ.pop("TWELVEDATA_API_KEY", None)
        env_path.write_text(
            "# комментарий\n\nTWELVEDATA_API_KEY=после-комментария\n", encoding="utf-8"
        )
        bridge = load_bridge()
        check(bridge.API_KEY == "после-комментария", "комментарии игнорируются", bridge.API_KEY)

        # Нет файла -> ключа нет, но мост не падает
        os.environ.pop("TWELVEDATA_API_KEY", None)
        env_path.unlink()
        bridge = load_bridge()
        check(bridge.API_KEY == "", "без .env ключ пустой, ошибки нет", repr(bridge.API_KEY))
    finally:
        if backup is not None:
            env_path.write_text(backup, encoding="utf-8")
        elif env_path.exists():
            env_path.unlink()
        if saved is not None:
            os.environ["TWELVEDATA_API_KEY"] = saved
        else:
            os.environ.pop("TWELVEDATA_API_KEY", None)


def test_endpoints() -> None:
    print("\n=== 3. Endpoints моста (без обращения к Twelve Data) ===")
    saved = os.environ.pop("TWELVEDATA_API_KEY", None)
    try:
        bridge = load_bridge()
        client = bridge.app.test_client()

        r = client.get("/health")
        body = r.get_json()
        check(r.status_code == 200 and body["status"] == "ok", "/health отвечает ok")
        check(body["api_key_present"] is False, "/health честно сообщает об отсутствии ключа")
        check(body["port"] == 8787, "/health показывает порт")

        # Без ключа сигнал обязан быть нейтральным — советник его проигнорирует
        r = client.get("/signal?symbol=XAUUSD")
        body = r.get_json()
        check(body["direction"] == "neutral" and body["confidence"] == 0.0,
              "без ключа /signal отдаёт neutral (безопасный отказ)", str(body))
    finally:
        if saved is not None:
            os.environ["TWELVEDATA_API_KEY"] = saved


def test_security() -> None:
    print("\n=== 4. Безопасность ===")
    source = BRIDGE.read_text(encoding="utf-8")
    check('HOST = "127.0.0.1"' in source, "мост слушает только локальный адрес")
    check("ВСТАВЬ_СВОЙ_КЛЮЧ" not in source, "ключа-заглушки в коде больше нет")

    gitignore = (BASE.parent / ".gitignore").read_text(encoding="utf-8")
    check("ai_scalper_pro/bridge/.env" in gitignore or ".env" in gitignore.split("\n"),
          ".env моста внесён в .gitignore")
    check((BRIDGE.parent / ".env.example").exists(), "есть .env.example без ключа")


def main() -> int:
    if not BRIDGE.exists():
        print(f"ОШИБКА: не найден файл моста {BRIDGE}")
        return 1
    bridge = load_bridge()
    test_symbols(bridge)
    test_env_reading()
    test_endpoints()
    test_security()

    print("\n===========================================")
    print(f"Пройдено: {passed}, провалено: {failed}")
    print("===========================================")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
