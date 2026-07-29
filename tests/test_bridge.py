#!/usr/bin/env python3
"""Тесты Python-моста: проверка ответа Claude, кэш, воспроизведение, endpoints.

Запуск:  python3 tests/test_bridge.py
Ключ API не нужен — обращений к Claude здесь нет.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent / "bridge"))

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


def test_validation() -> None:
    from claude_client import validate_response

    print("\n=== 1. Проверка ответа Claude (строгая) ===")
    good = {"regime": "range", "trade_allowed": True, "risk_multiplier": 0.5, "reason": "ок"}
    check(validate_response(good)[0], "корректный ответ принят")

    for regime in ("trend_up", "trend_down", "range", "chaos"):
        r = dict(good, regime=regime)
        check(validate_response(r)[0], f"режим {regime} принят")

    check(not validate_response(dict(good, regime="sideways"))[0], "неизвестный режим отклонён")
    check(not validate_response(dict(good, risk_multiplier=1.5))[0],
          "risk_multiplier 1.5 отклонён (ИИ не может увеличить риск)")
    check(not validate_response(dict(good, risk_multiplier=-0.1))[0],
          "отрицательный risk_multiplier отклонён")
    check(validate_response(dict(good, risk_multiplier=0.0))[0], "risk_multiplier 0.0 допустим")
    check(validate_response(dict(good, risk_multiplier=1.0))[0], "risk_multiplier 1.0 допустим")
    check(not validate_response(dict(good, trade_allowed="yes"))[0], "строка вместо bool отклонена")
    # В Python True == 1, поэтому bool отдельно исключается из чисел
    check(not validate_response(dict(good, risk_multiplier=True))[0],
          "bool вместо числа отклонён")
    for field in ("regime", "trade_allowed", "risk_multiplier", "reason"):
        broken = {k: v for k, v in good.items() if k != field}
        check(not validate_response(broken)[0], f"ответ без поля {field} отклонён")
    check(not validate_response("не словарь")[0], "не-объект отклонён")
    check(not validate_response([1, 2, 3])[0], "список отклонён")


def test_json_extraction() -> None:
    from claude_client import _extract_json

    print("\n=== 2. Извлечение JSON из ответа модели ===")
    check(_extract_json('{"a": 1}') == {"a": 1}, "чистый JSON")
    check(_extract_json('Вот ответ: {"a": 1}. Всё.') == {"a": 1}, "JSON в тексте")
    check(_extract_json('```json\n{"a": 1}\n```') == {"a": 1}, "JSON в блоке кода")
    check(_extract_json("совсем не json") is None, "мусор -> None")
    check(_extract_json('{"сломан": ') is None, "битый JSON -> None")


def test_cache() -> None:
    import cache
    from config import CONFIG

    print("\n=== 3. Кэш: раздельный для каждого символа ===")
    cache._cache.clear()
    eur = {"regime": "trend_up", "trade_allowed": True, "risk_multiplier": 1.0, "reason": "eur"}
    gold = {"regime": "chaos", "trade_allowed": False, "risk_multiplier": 0.0, "reason": "gold"}

    cache.cache_put("EURUSD", eur)
    cache.cache_put("XAUUSD", gold)
    check(cache.cache_get("EURUSD")[0]["reason"] == "eur", "EURUSD берёт свой ответ")
    check(cache.cache_get("XAUUSD")[0]["reason"] == "gold", "XAUUSD берёт свой ответ")
    check(cache.cache_get("EURUSD")[0] is not cache.cache_get("XAUUSD")[0],
          "кэши двух символов не смешиваются")
    check(cache.cache_get("GBPUSD")[0] is None, "неизвестный символ -> пусто")

    print("\n=== 4. Кэш: срок жизни ===")
    original_ttl = CONFIG["cache"]["ttl_minutes"]
    check(cache.cache_fresh("EURUSD") is not None, "свежий ответ отдаётся")
    # Состариваем запись искусственно
    cache._cache["EURUSD"]["saved_at"] = time.time() - (original_ttl * 60 + 10)
    check(cache.cache_fresh("EURUSD") is None, "просроченный ответ не отдаётся как свежий")
    check(cache.cache_get("EURUSD")[0] is not None,
          "но сам ответ сохраняется (советник решит по возрасту)")
    age = cache.cache_get("EURUSD")[1]
    check(age > original_ttl * 60, f"возраст кэша считается верно ({int(age)} сек)")


def test_replay() -> None:
    import cache
    from config import CONFIG

    print("\n=== 5. Режим воспроизведения записанных ответов ===")
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "replay.jsonl"
        records = [
            {"symbol": "EURUSD", "valid": True,
             "response": {"regime": "range", "trade_allowed": True,
                          "risk_multiplier": 0.5, "reason": "первый"}},
            {"symbol": "XAUUSD", "valid": True,
             "response": {"regime": "chaos", "trade_allowed": False,
                          "risk_multiplier": 0.0, "reason": "золото"}},
            {"symbol": "EURUSD", "valid": False, "response": None},
            {"symbol": "EURUSD", "valid": True,
             "response": {"regime": "trend_up", "trade_allowed": True,
                          "risk_multiplier": 1.0, "reason": "последний"}},
        ]
        log.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
            encoding="utf-8",
        )

        CONFIG["replay"]["enabled"] = True
        CONFIG["replay"]["file"] = str(log)

        eur = cache.load_replay_response("EURUSD")
        check(eur is not None and eur["reason"] == "последний",
              "берётся ПОСЛЕДНИЙ валидный ответ символа",
              str(eur))
        gold = cache.load_replay_response("XAUUSD")
        check(gold is not None and gold["reason"] == "золото", "ответ для золота отдельный")
        check(cache.load_replay_response("GBPUSD") is None, "нет записей -> None")

        CONFIG["replay"]["file"] = str(Path(tmp) / "нет-файла.jsonl")
        check(cache.load_replay_response("EURUSD") is None, "отсутствующий файл -> None")

        CONFIG["replay"]["enabled"] = False
        CONFIG["replay"]["file"] = str(log)
        check(cache.load_replay_response("EURUSD") is None, "режим выключен -> None")


def test_endpoints() -> None:
    from fastapi.testclient import TestClient

    from config import CONFIG

    print("\n=== 6. Endpoints моста (mock-режим, без обращения к Claude) ===")
    CONFIG["mock"].update(
        {"enabled": True, "regime": "trend_down", "trade_allowed": True,
         "risk_multiplier": 0.3, "reason": "мок"}
    )
    import main

    client = TestClient(main.app)

    r = client.get("/health")
    check(r.status_code == 200 and r.json()["status"] == "ok", "/health отвечает ok")
    check("mock_mode" in r.json(), "/health сообщает про режим mock")
    check(r.json()["api_key_present"] is False, "/health честно говорит, что ключа нет")

    r = client.get("/regime?symbol=EURUSD")
    body = r.json()
    check(r.status_code == 200 and body["regime"] == "trend_down", "/regime EURUSD отвечает")
    check(body["risk_multiplier"] == 0.3, "risk_multiplier передаётся как есть")

    r = client.get("/regime?symbol=xauusd")
    check(r.status_code == 200, "символ в нижнем регистре принимается")

    r = client.get("/regime?symbol=BTCUSD")
    check(r.status_code == 400, "неподдерживаемый символ -> ошибка 400")

    r = client.get("/regime")
    check(r.status_code == 422, "запрос без символа -> ошибка")

    # Мок с недопустимым значением обязан превратиться в безопасный chaos
    CONFIG["mock"]["risk_multiplier"] = 99.0
    r = client.get("/regime?symbol=EURUSD")
    body = r.json()
    check(body["regime"] == "chaos" and body["trade_allowed"] is False,
          "недопустимый мок -> безопасный chaos, торговля запрещена", str(body))
    CONFIG["mock"]["risk_multiplier"] = 0.3
    CONFIG["mock"]["enabled"] = False


def test_config_safety() -> None:
    print("\n=== 7. Безопасность конфигурации ===")
    import config

    cfg = config.load_config()
    check(cfg["server"]["host"] == "127.0.0.1", "мост слушает только локальный адрес")

    # Даже если в конфиге попросили открыть наружу — должно быть принудительно закрыто
    original = config.DEFAULTS["server"]["host"]
    config.DEFAULTS["server"]["host"] = "0.0.0.0"
    cfg = config.load_config()
    check(cfg["server"]["host"] == "127.0.0.1",
          "попытка открыть мост наружу принудительно отклоняется")
    config.DEFAULTS["server"]["host"] = original

    gitignore = (BASE.parent / ".gitignore").read_text(encoding="utf-8")
    check(".env" in gitignore, ".env внесён в .gitignore")
    check("config.toml" in gitignore, "config.toml внесён в .gitignore")
    check((BASE.parent / "bridge" / ".env.example").exists(), "есть .env.example без ключа")
    example = (BASE.parent / "bridge" / ".env.example").read_text(encoding="utf-8")
    check(example.strip().endswith("="), "в .env.example ключ пустой")


def main_run() -> int:
    test_validation()
    test_json_extraction()
    test_cache()
    test_replay()
    test_endpoints()
    test_config_safety()

    print("\n===========================================")
    print(f"Пройдено: {passed}, провалено: {failed}")
    print("===========================================")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main_run())
