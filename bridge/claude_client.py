"""Запрос к Claude и СТРОГАЯ проверка ответа.

Claude используется только как ограничитель риска. Любое отклонение от
ожидаемого формата делает ответ недействительным — советник в этом случае
опирается на кэш или блокирует входы.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

from config import CONFIG

VALID_REGIMES = {"trend_up", "trend_down", "range", "chaos"}

SYSTEM_PROMPT = """Ты — фильтр рыночного режима для консервативной торговой системы.
Ты НЕ даёшь торговых сигналов. Твоя единственная задача — классифицировать
текущий режим рынка и, при необходимости, ОГРАНИЧИТЬ торговлю.

Правила:
- Если данных мало, они устарели или картина противоречива — выбирай "chaos".
- risk_multiplier может только уменьшать объём (0.0..1.0), 1.0 = без ограничения.
- Отвечай ТОЛЬКО валидным JSON без каких-либо пояснений вокруг, строго в формате:
{"regime": "trend_up|trend_down|range|chaos", "trade_allowed": true|false,
 "risk_multiplier": 0.0..1.0, "reason": "краткое пояснение по-русски"}"""


def validate_response(data: Any) -> tuple[bool, str]:
    """Программная проверка ответа. Возвращает (валиден, причина ошибки)."""
    if not isinstance(data, dict):
        return False, "ответ не является JSON-объектом"
    required = {"regime", "trade_allowed", "risk_multiplier", "reason"}
    missing = required - set(data.keys())
    if missing:
        return False, f"отсутствуют поля: {sorted(missing)}"
    if data["regime"] not in VALID_REGIMES:
        return False, f"недопустимый regime: {data['regime']!r}"
    if not isinstance(data["trade_allowed"], bool):
        return False, "trade_allowed не является булевым значением"
    if not isinstance(data["risk_multiplier"], (int, float)) or isinstance(data["risk_multiplier"], bool):
        return False, "risk_multiplier не является числом"
    if not 0.0 <= float(data["risk_multiplier"]) <= 1.0:
        return False, f"risk_multiplier вне диапазона 0..1: {data['risk_multiplier']}"
    if not isinstance(data["reason"], str):
        return False, "reason не является строкой"
    return True, ""


def _extract_json(text: str) -> Any | None:
    """Достаёт JSON-объект из текста ответа модели."""
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def build_prompt(snapshot: dict, news: dict) -> str:
    parts = [
        f"Инструмент: {snapshot['requested_symbol']} (символ брокера: {snapshot['broker_symbol']}).",
        "Оцени режим рынка по данным ниже.",
        "",
        "=== Последние закрытые свечи H1 (до 48) ===",
        json.dumps(snapshot["h1"], ensure_ascii=False),
        "",
        "=== Последние закрытые свечи H4 (до 30) ===",
        json.dumps(snapshot["h4"], ensure_ascii=False),
        "",
        "=== Актуальная цена ===",
        json.dumps(snapshot["tick"], ensure_ascii=False),
        "",
        "=== Новости на сегодня ===",
        json.dumps(news, ensure_ascii=False),
    ]
    if snapshot["requested_symbol"] == "XAUUSD":
        dxy = snapshot.get("dxy", {"available": False})
        us10y = snapshot.get("us10y", {"available": False})
        parts += [
            "",
            "=== Контекст для золота ===",
            "Индекс доллара (DXY): "
            + (json.dumps(dxy, ensure_ascii=False) if dxy.get("available") else "ДАННЫЕ НЕДОСТУПНЫ"),
            "Доходность US10Y: "
            + (json.dumps(us10y, ensure_ascii=False) if us10y.get("available") else "ДАННЫЕ НЕДОСТУПНЫ"),
            "Если контекстные данные недоступны или устарели (stale=true) — учитывай это как фактор неопределённости.",
        ]
    parts += ["", "Ответь строго одним JSON-объектом."]
    return "\n".join(parts)


def ask_claude(snapshot: dict, news: dict) -> tuple[dict | None, str, str]:
    """Возвращает (валидный_ответ | None, сырой_текст, ошибка)."""
    cfg = CONFIG["anthropic"]
    if not cfg["api_key"]:
        return None, "", "ANTHROPIC_API_KEY не задан в .env"

    client = anthropic.Anthropic(api_key=cfg["api_key"], timeout=cfg["timeout_seconds"])
    prompt = build_prompt(snapshot, news)

    try:
        message = client.messages.create(
            model=cfg["model"],
            max_tokens=cfg["max_tokens"],
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        return None, "", f"ошибка API Anthropic: {exc}"

    raw = "".join(block.text for block in message.content if block.type == "text")
    data = _extract_json(raw)
    if data is None:
        return None, raw, "в ответе модели нет валидного JSON"

    ok, err = validate_response(data)
    if not ok:
        return None, raw, f"ответ не прошёл проверку: {err}"

    # Нормализуем типы
    data["risk_multiplier"] = float(data["risk_multiplier"])
    return data, raw, ""
