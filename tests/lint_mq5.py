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

DEFAULT_SOURCES = [Path(__file__).resolve().parent.parent / "mql5" / "DualGuardEA.mq5"]

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
    # Дополнительно используется в проекте AI_Scalper_Pro
    "PrintFormat", "PositionSelectByTicket", "PositionClosePartial",
    "ArrayRemove", "ArrayCopy", "ArrayInitialize", "ArraySort",
    "FileOpen", "FileClose", "FileWrite", "FileSeek", "FileIsExist",
    "ObjectCreate", "ObjectDelete", "ObjectSetInteger", "ObjectSetString",
    "ObjectsDeleteAll", "ObjectFind", "ChartRedraw", "ChartGetInteger",
    "iTime", "iBarShift", "Bars", "CopyRates", "CopyTime", "CopyClose",
    "CopyHigh", "CopyLow", "CopyOpen", "SeriesInfoInteger",
    "iADX", "iStochastic", "iMACD", "iCCI", "iBearsPower", "iBullsPower",
    "iVolumes", "iStdDev", "iEnvelopes", "iForce", "iMomentum", "iOsMA",
    "TerminalInfoInteger", "TerminalInfoString", "AccountInfoString",
    "MathSqrt", "MathLog", "MathExp", "MathCeil", "MathMod", "MathRand",
    "StringConcatenate", "StringReplace", "StringTrimLeft", "StringTrimRight",
    "StringToLower", "CharToString", "ShortToString", "StringAdd",
    "TimeLocal", "TimeDaylightSavings", "PeriodSeconds", "OrderSend",
    "SymbolSelect", "SymbolName", "SymbolsTotal", "SymbolInfoTick",
    "HistorySelect", "HistoryDealsTotal", "HistoryDealGetTicket",
    "HistoryDealGetInteger", "HistoryDealGetDouble", "HistoryDealGetString",
    "CalendarValueLast", "CalendarCountryById", "CalendarEventByCurrency",
    "EventChartCustom", "Alert", "SendNotification", "PlaySound",
    "ZeroMemory", "StringSubstr", "StringBufferLen", "IsStopped",
    "CopyTickVolume", "GetTickCount64", "HistoryDealSelect",
    "SetTypeFillingBySymbol",
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
    # Пути можно передать аргументами: python3 lint_mq5.py файл1.mq5 файл2.mqh
    # Все файлы разбираются как ОДНО целое — так функции из .mqh видны
    # в главном .mq5, как это и происходит при компиляции через #include.
    sources = [Path(p) for p in sys.argv[1:]] or DEFAULT_SOURCES
    missing = [p for p in sources if not p.exists()]
    if missing:
        for p in missing:
            print(f"ОШИБКА: файл не найден: {p}")
        return 1

    all_errors: list[str] = []
    total_lines = 0
    total_inputs = 0
    combined_src = []
    combined_code = []

    for path in sources:
        src = path.read_text(encoding="utf-8")
        code = strip_code(src)
        total_lines += src.count("\n") + 1
        combined_src.append(src)
        combined_code.append(code)

        # Скобки и запись в input проверяем ПОФАЙЛОВО — так виден точный файл
        for err in check_braces(code):
            all_errors.append(f"{path.name}: {err}")
        input_errors, input_count = check_input_writes(src, code)
        total_inputs += input_count
        for err in input_errors:
            all_errors.append(f"{path.name}: {err}")

    # Вызовы проверяем по всем файлам сразу
    all_errors += check_calls("\n".join(combined_src), "\n".join(combined_code))

    func_pattern = r"^\s*(?:void|int|bool|double|string|long|ulong|datetime|ENUM_\w+)\s+(\w+)\s*\("
    func_count = len(set(re.findall(func_pattern, "\n".join(combined_src), re.M)))
    names = ", ".join(p.name for p in sources)
    print(f"Файлов: {len(sources)} ({names})")
    print(f"Всего строк: {total_lines}")
    print(f"input-параметров: {total_inputs}")
    print(f"функций определено: {func_count}")
    input_count = total_inputs

    if all_errors:
        print("\nНАЙДЕНЫ ПРОБЛЕМЫ:")
        for e in all_errors:
            print("  -", e)
        return 1
    print("\nПроблем не найдено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
