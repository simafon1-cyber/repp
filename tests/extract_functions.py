#!/usr/bin/env python3
"""Извлекает «чистые» функции из DualGuardEA.mq5 в заголовок для C++-тестов.

Смысл: тесты проверяют НАСТОЯЩИЙ код советника, а не его копию.
Если функцию в .mq5 изменить или переименовать — извлечение упадёт,
и тест это заметит.
"""

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
SOURCE = BASE.parent / "mql5" / "DualGuardEA.mq5"
OUTPUT = BASE / "generated_functions.h"

# Функции, не зависящие от торгового API MT5 — их можно выполнить в C++
WANTED = [
    "NormalizeMinutes",
    "ParseHHMM",
    "DayOfWeekOf",
    "MakeDate",
    "LastSundayOfMonth",
    "NthSundayOfMonth",
    "IsEUDst",
    "IsUSDst",
    "IsInGoldSession",
    "IsNearWeekClose",
    "JsonGetRaw",
    "ParseBridgeResponse",
    "VolumeDigits",
    "FloorToStep",
    "ProfileRiskPercent",
    "LossPerLot",
    "CalcLotByRisk",
    "GetAIGate",
]


def extract(source: str, name: str) -> str:
    """Вырезает тело функции по имени, считая фигурные скобки."""
    pattern = re.compile(
        r"^[A-Za-z_][A-Za-z0-9_ ]*\b" + re.escape(name) + r"\s*\(", re.MULTILINE
    )
    match = pattern.search(source)
    if match is None:
        raise SystemExit(f"ОШИБКА: функция {name} не найдена в {SOURCE.name}")

    start = match.start()
    i = source.index("{", match.end())
    depth = 0
    while i < len(source):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
        elif ch == '"':  # пропускаем строковые литералы
            i += 1
            while i < len(source) and source[i] != '"':
                i += 2 if source[i] == "\\" else 1
        elif ch == "'":
            i += 1
            while i < len(source) and source[i] != "'":
                i += 2 if source[i] == "\\" else 1
        elif source.startswith("//", i):
            i = source.index("\n", i)
        i += 1
    raise SystemExit(f"ОШИБКА: не удалось найти конец функции {name}")


def wrap_string_literals(code: str) -> str:
    """Оборачивает строковые литералы в mqlstr(...).

    В MQL5 литералы имеют тип `string`, поэтому `"a" + переменная` работает.
    В C++ это указатели, и сложение не компилируется. Разбор посимвольный:
    символьный литерал '"' (он есть в парсере JSON) не должен приниматься
    за начало строки.
    """
    out = []
    i, n = 0, len(code)
    while i < n:
        ch = code[i]
        if code.startswith("//", i):
            end = code.find("\n", i)
            end = n if end < 0 else end
            out.append(code[i:end])
            i = end
        elif ch == '"':
            start = i
            i += 1
            while i < n and code[i] != '"':
                i += 2 if code[i] == "\\" else 1
            i += 1
            out.append("mqlstr(" + code[start:i] + ")")
        elif ch == "'":
            start = i
            i += 1
            while i < n and code[i] != "'":
                i += 2 if code[i] == "\\" else 1
            i += 1
            out.append(code[start:i])
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def to_cpp(code: str) -> str:
    """Мелкие правки синтаксиса, которого нет в C++.

    1. Динамический массив MQL5 `string parts[];` -> MqlArray<T>.
    2. Строковые литералы -> mqlstr(...), чтобы работало сложение строк.
    """
    code = re.sub(
        r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)\[\]\s*;",
        r"\1MqlArray<\2> \3;",
        code,
        flags=re.MULTILINE,
    )
    return wrap_string_literals(code)


def main() -> None:
    # Без аргументов — советник DualGuard (набор WANTED выше).
    # С аргументами: extract_functions.py ИСХОДНИК ВЫХОД функция1 функция2 ...
    if len(sys.argv) > 3:
        source_path = Path(sys.argv[1])
        output_path = Path(sys.argv[2])
        wanted = sys.argv[3:]
    else:
        source_path, output_path, wanted = SOURCE, OUTPUT, WANTED

    if not source_path.exists():
        raise SystemExit(f"ОШИБКА: не найден файл {source_path}")

    source = source_path.read_text(encoding="utf-8")
    parts = [f"// СГЕНЕРИРОВАНО автоматически из {source_path.name} — не редактировать\n"]
    for name in wanted:
        parts.append(f"// ---- {name} ----")
        parts.append(to_cpp(extract(source, name)))
        parts.append("")
    output_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"Извлечено функций: {len(wanted)} -> {output_path.name}")


if __name__ == "__main__":
    sys.exit(main())
