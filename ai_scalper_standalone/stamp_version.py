"""stamp_version.py — вписывает номер сборки в version.py перед упаковкой .exe.

Запускается сборочным сценарием (.github/workflows/build-exe.yml):

    python stamp_version.py <номер сборки> <номер правки> <дата>

Почему отдельным файлом, а не парой строк прямо в сценарии сборки: то, что
написано в сценарии, проверить нечем — оно выполняется только на сервере
GitHub, и ошибка в нём обнаружилась бы уже готовым .exe без версии. Здесь же
обычная функция, у которой есть тест (tests/test_selfupdate.py).

Правка точечная: заменяются ТОЛЬКО три значения. Всё остальное в version.py —
пояснения и функции, которыми пользуется программа, — остаётся как есть.
"""

import os
import re
import sys

FIELDS = ("BUILD_NUMBER", "COMMIT", "BUILT_AT")


def stamp(text: str, build: str, commit: str, built_at: str) -> str:
    """Возвращает содержимое version.py с подставленными значениями."""
    values = {
        "BUILD_NUMBER": str(build).strip(),
        "COMMIT": str(commit).strip()[:7],
        "BUILT_AT": str(built_at).strip(),
    }
    for name, value in values.items():
        pattern = re.compile(rf'^{name}\s*=.*$', re.MULTILINE)
        replacement = f'{name} = {value!r}'
        if not pattern.search(text):
            raise ValueError(f"version.py has no {name} line - file changed?")
        # re.sub трактует \ и \g в замене как спецсимволы; в номере правки и
        # дате их быть не должно, но подставляем через функцию, чтобы это не
        # зависело от содержимого вовсе.
        text = pattern.sub(lambda _m, r=replacement: r, text, count=1)
    return text


def main(argv) -> int:
    if len(argv) != 4:
        print("usage: stamp_version.py <build> <commit> <date>")
        return 2
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "version.py")
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    text = stamp(text, argv[1], argv[2], argv[3])
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    # ТОЛЬКО латиница. Консоль Windows на серверах GitHub работает в cp1252,
    # и кириллица в print роняет весь шаг сборки с UnicodeEncodeError —
    # проверено вживую, сборка 11 упала ровно на этой строке.
    print(f"version stamped: build={argv[1]} commit={argv[2][:7]} date={argv[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
