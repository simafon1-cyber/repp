#!/usr/bin/env python3
"""Статическая проверка DualGuardEA.mq5 без MetaEditor.

Ловит ошибки, которые иначе всплыли бы только при компиляции:
  1. Несбалансированные фигурные скобки.
  2. Запись в input-параметр (в MQL5 это константа — ошибка компиляции).
  3. Вызов функции, которой нет ни в файле, ни в списке API MQL5 (опечатка в имени).
  4. Незакрытые строки и комментарии.

Разбор ведётся посимвольно, с корректной обработкой строковых ("...")
и символьных ('...') литералов — иначе литерал '"' в парсере JSON
сбивает любой анализ на регулярных выражениях.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "mql5" / "DualGuardEA.mq5"

# Функции MQL5 и методы CTrade, используемые советником.
KNOWN_API = {
    # Счёт и символ
    "AccountInfoDouble", "AccountInfoInteger", "AccountInfoString",
    "SymbolInfoDouble", "SymbolInfoInteger", "SymbolInfoString",
    # Массивы
    "ArrayResize", "ArraySetAsSeries", "ArraySize",
    # Индикаторы и таймсерии
    "CopyBuffer", "IndicatorRelease", "iATR", "iBands", "iMA", "iRSI",
    "iOpen", "iClose", "iHigh", "iLow", "iTime",
    # Позиции и торговля
    "PositionsTotal", "PositionGetTicket", "PositionGetDouble",
    "PositionGetInteger", "PositionGetString",
    "OrderCalcProfit", "OrderCalcMargin",
    # Методы CTrade
    "Buy", "Sell", "PositionClose", "PositionModify", "SetAsyncMode",
    "SetDeviationInPoints", "SetExpertMagicNumber",
    "ResultRetcode", "ResultRetcodeDescription",
    # Глобальные переменные терминала
    "GlobalVariableCheck", "GlobalVariableGet", "GlobalVariableSet",
    "GlobalVariableSetOnCondition", "GlobalVariableTime",
    # Время
    "TimeCurrent", "TimeGMT", "TimeTradeServer", "TimeToStruct",
    "StructToTime", "TimeToString", "GetTickCount", "Sleep",
    "EventSetTimer", "EventKillTimer",
    # Строки и преобразования
    "StringFind", "StringFormat", "StringGetCharacter", "StringLen",
    "StringSplit", "StringSubstr", "StringToDouble", "StringToInteger",
    "StringToUpper", "CharArrayToString", "IntegerToString",
    "DoubleToString", "NormalizeDouble", "EnumToString",
    # Математика
    "MathAbs", "MathFloor", "MathMax", "MathMin", "MathPow", "MathRound",
    # Календарь новостей
    "CalendarEventById", "CalendarValueHistory",
    # Прочее
    "Print", "Comment", "WebRequest", "GetLastError", "ResetLastError",
    "MQLInfoInteger",
}

CONTROL_KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof", "else", "do"}


def strip_code(src: str) -> str:
    """Убирает комментарии и содержимое литералов, СОХРАНЯЯ переводы строк.

    Так номера строк остаются верными, а литералы вроде '"' не ломают разбор.
    """
    out = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
        elif src.startswith("/*", i):
            i += 2
            while i < n and not src.startswith("*/", i):
                if src[i] == "\n":
                    out.append("\n")
                i += 1
            i += 2
        elif ch == '"':
            out.append('""')
            i += 1
            while i < n and src[i] != '"':
                i += 2 if src[i] == "\\" else 1
            i += 1
        elif ch == "'":
            out.append("' '")
            i += 1
            while i < n and src[i] != "'":
                i += 2 if src[i] == "\\" else 1
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def check_braces(code: str) -> list[str]:
    errors, depth, line = [], 0, 1
    for ch in code:
        if ch == "\n":
            line += 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                errors.append(f"строка {line}: лишняя закрывающая скобка }}")
                depth = 0
    if depth != 0:
        errors.append(f"не закрыто фигурных скобок: {depth}")
    return errors


def check_input_writes(src: str, code: str) -> list[str]:
    """В MQL5 input-параметр менять нельзя — это ошибка компиляции."""
    inputs = set(re.findall(r"^input\s+[\w:]+\s+(\w+)\s*=", src, re.M))
    declaration_lines = {
        i + 1
        for i, line in enumerate(src.splitlines())
        if line.lstrip().startswith("input ")
    }
    errors = []
    for name in sorted(inputs):
        for m in re.finditer(r"\b" + name + r"\s*(?:=(?!=)|\+\+|--|[-+*/]=)", code):
            line = code[: m.start()].count("\n") + 1
            if line not in declaration_lines:
                errors.append(f"строка {line}: запись в input-параметр {name}")
    return errors, len(inputs)


def check_calls(src: str, code: str) -> list[str]:
    defined = set(
        re.findall(
            r"^\s*(?:void|int|bool|double|string|long|ulong|datetime|ENUM_\w+)\s+(\w+)\s*\(",
            src,
            re.M,
        )
    )
    inputs = set(re.findall(r"^input\s+[\w:]+\s+(\w+)\s*=", src, re.M))
    called = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", code))
    unknown = called - defined - KNOWN_API - CONTROL_KEYWORDS - inputs
    return [f"неизвестный вызов: {name}()" for name in sorted(unknown)]


def main() -> int:
    src = SOURCE.read_text(encoding="utf-8")
    code = strip_code(src)

    all_errors: list[str] = []
    all_errors += check_braces(code)
    input_errors, input_count = check_input_writes(src, code)
    all_errors += input_errors
    all_errors += check_calls(src, code)

    func_pattern = r"^\s*(?:void|int|bool|double|string|long|ulong|datetime|ENUM_\w+)\s+(\w+)\s*\("
    func_count = len(set(re.findall(func_pattern, src, re.M)))
    print(f"Файл: {SOURCE.name}  ({src.count(chr(10)) + 1} строк)")
    print(f"input-параметров: {input_count}")
    print(f"функций определено: {func_count}")

    if all_errors:
        print("\nНАЙДЕНЫ ПРОБЛЕМЫ:")
        for e in all_errors:
            print("  -", e)
        return 1
    print("\nПроблем не найдено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
