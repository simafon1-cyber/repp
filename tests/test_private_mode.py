#!/usr/bin/env python3
"""Тесты приватного режима.

Главное, что здесь проверяется:

  1. Приватный режим открывает ТОЛЬКО config.py. Живые пропуска —
     telegram_session (доступ к Telegram), accounts.json (пароли счетов),
     журналы и CSV сделок — остаются вне git при любом режиме.
  2. Все места записи секретов идут через одну точку (protect_secret), а не
     вызывают шифрование напрямую: иначе добавление режима означало бы
     правку каждого места поодиночке, и забыть одно — вопрос времени.
  3. В приватном режиме не запускается миграция, которая зашифровала бы
     секреты обратно.

Запуск:  python3 tests/test_private_mode.py
"""

from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
APP = ROOT / "ai_scalper_standalone"
INSTALL = ROOT / "install"
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


def install_stubs() -> types.ModuleType:
    cfg = types.ModuleType("config")
    exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
    sys.modules["config"] = cfg
    return cfg


CFG = install_stubs()

import secure_store as ss   # noqa: E402


# =====================================================================
# 1. Поведение приватного режима
# =====================================================================
def test_private_mode_flag() -> None:
    print("\n[Переключатель]")

    CFG.PRIVATE_MODE = False
    check(ss.private_mode() is False, "Выключен по умолчанию в config.py.example")

    CFG.PRIVATE_MODE = True
    check(ss.private_mode() is True, "Включается настройкой")

    # Значение по умолчанию в поставляемом конфиге — выключено: включать
    # режим можно только осознанно, после того как репозиторий стал приватным
    default_cfg = types.ModuleType("c")
    exec((APP / "config.py.example").read_text(encoding="utf-8"), default_cfg.__dict__)
    check(default_cfg.PRIVATE_MODE is False,
          "В поставляемом config.py.example режим выключен")


def test_protect_secret() -> None:
    print("\n[Запись секрета]")

    salt = ss.new_salt()
    password = "тестовый-пароль"

    CFG.PRIVATE_MODE = True
    stored = ss.protect_secret("мой-ключ-12345", password, salt)
    check(stored == "мой-ключ-12345", "Приватный режим: значение как есть", stored)
    check(not stored.startswith(ss.ENC_PREFIX), "Префикса шифрования нет")

    CFG.PRIVATE_MODE = False
    stored = ss.protect_secret("мой-ключ-12345", password, salt)
    check(stored.startswith(ss.ENC_PREFIX), "Обычный режим: значение зашифровано", stored[:12])
    check(ss.decrypt_value(stored, password, salt) == "мой-ключ-12345",
          "И расшифровывается обратно тем же паролем")

    # Открытое значение читается в обоих режимах: decrypt_value пропускает
    # всё без префикса. Значит переключение режима не ломает уже записанное.
    check(ss.decrypt_value("мой-ключ-12345", password, salt) == "мой-ключ-12345",
          "Открытое значение читается и в обычном режиме — переключение не ломает файл")

    CFG.PRIVATE_MODE = True
    check(ss.protect_secret("", password, salt) == "", "Пустое значение остаётся пустым")
    CFG.PRIVATE_MODE = False


def test_no_password_needed() -> None:
    print("\n[Пароль для чтения не нужен]")

    CFG.PRIVATE_MODE = True
    salt = ss.new_salt()

    fake_cfg = types.ModuleType("cfg2")
    fake_cfg.SECURITY_SALT = salt
    fake_cfg.MT5_PASSWORD = "пароль-мт5"
    fake_cfg.ANTHROPIC_API_KEY = "sk-ant-пример"
    fake_cfg.OPENAI_API_KEY = ""
    fake_cfg.NEWS_API_KEYS = {"finnhub": "ключ-финхаб"}

    # unlock_config с ПУСТЫМ паролем не должен ничего испортить: значения
    # открытые, расшифровывать нечего. Это и есть выход из прежнего тупика —
    # раньше без пароля ключи оставались недоступными.
    ss.unlock_config(fake_cfg, "")
    check(fake_cfg.MT5_PASSWORD == "пароль-мт5", "Пароль MT5 читается без пароля входа")
    check(fake_cfg.ANTHROPIC_API_KEY == "sk-ant-пример", "Ключ AI читается без пароля входа")
    check(fake_cfg.NEWS_API_KEYS["finnhub"] == "ключ-финхаб", "Ключ новостей читается")

    check(ss.has_encrypted_secrets(fake_cfg) is False,
          "Зашифрованных секретов нет — программа не будет просить пароль")
    CFG.PRIVATE_MODE = False


# =====================================================================
# 2. ГЛАВНОЕ: что режим НЕ открывает
# =====================================================================
def parse_gitignore_block():
    """Возвращает (строки внутри блока приватного режима, строки вне него)."""
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    inside, outside = [], []
    in_block = False
    for line in lines:
        if "НАЧАЛО БЛОКА ПРИВАТНОГО РЕЖИМА" in line:
            in_block = True
            continue
        if "КОНЕЦ БЛОКА ПРИВАТНОГО РЕЖИМА" in line:
            in_block = False
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        (inside if in_block else outside).append(stripped)
    return inside, outside


def test_gitignore_block() -> None:
    print("\n[Что открывает приватный режим]")

    raw = (ROOT / ".gitignore").read_text(encoding="utf-8")
    check("НАЧАЛО БЛОКА ПРИВАТНОГО РЕЖИМА" in raw, "Метка начала блока на месте")
    check("КОНЕЦ БЛОКА ПРИВАТНОГО РЕЖИМА" in raw, "Метка конца блока на месте")

    inside, outside = parse_gitignore_block()
    check(bool(inside), "Внутри блока есть правила", str(inside))

    # ВНУТРИ блока может быть только config.py и его производные
    for rule in inside:
        check("config.py" in rule,
              f"Внутри блока только config.py: {rule}", rule)

    # СНАРУЖИ — всё, что не должно попасть в git ни при каком режиме
    must_stay = [
        "telegram_session",      # живой пропуск в Telegram
        "accounts.json",         # логины и пароли счетов MT5
        ".login_remember",       # сохранённый пароль входа
        "trades_log.csv",        # номера счетов и балансы
        "learning_state.json",   # личная статистика
        "*.log",                 # журналы с номерами счетов
    ]
    joined_outside = " ".join(outside)
    for name in must_stay:
        check(name in joined_outside,
              f"{name} остаётся вне git при ЛЮБОМ режиме")
        check(not any(name in rule for rule in inside),
              f"{name} НЕ попал внутрь переключаемого блока")


def test_example_config_still_tracked() -> None:
    print("\n[Шаблон конфига]")

    inside, outside = parse_gitignore_block()
    all_rules = inside + outside
    check(not any(r.rstrip("/") == "config.py.example" for r in all_rules),
          "config.py.example не игнорируется — он нужен для первого запуска")
    check((APP / "config.py.example").exists(), "Шаблон на месте")

    # В шаблоне не должно быть настоящих секретов
    text = (APP / "config.py.example").read_text(encoding="utf-8")
    tree = ast.parse(text)
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                values[node.targets[0].id] = ast.literal_eval(node.value)
            except Exception:
                pass
    for field in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MT5_PASSWORD",
                  "SECURITY_SALT", "DASHBOARD_PASSWORD_HASH", "TELEGRAM_API_HASH"):
        if field in values:
            check(values[field] in ("", 0), f"{field} в шаблоне пустой", repr(values[field]))
    if "TELEGRAM_API_ID" in values:
        check(values["TELEGRAM_API_ID"] == 0, "TELEGRAM_API_ID в шаблоне нулевой")
    if "MT5_LOGIN" in values:
        check(values["MT5_LOGIN"] == 0, "MT5_LOGIN в шаблоне нулевой")


# =====================================================================
# 3. Единая точка записи секретов
# =====================================================================
def test_single_write_path() -> None:
    print("\n[Секреты пишутся через одну точку]")

    gui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    tree = ast.parse(gui)

    # Прямые вызовы encrypt_value допустимы ТОЛЬКО в одноразовой миграции
    # старого конфига — она в приватном режиме и не запускается.
    migration = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and "migrate" in node.name.lower():
            migration = ast.get_source_segment(gui, node)

    check(migration is not None, "Функция миграции найдена")
    if migration:
        check("secure_store.private_mode()" in migration,
              "Миграция в приватном режиме не запускается — иначе зашифровала бы "
              "обратно то, что вы хотите держать открытым")

    outside_migration = gui.replace(migration or "", "")
    check("secure_store.encrypt_value" not in outside_migration,
          "Вне миграции прямых вызовов шифрования не осталось")
    check(outside_migration.count("secure_store.protect_secret") >= 3,
          "Все места записи секретов идут через protect_secret",
          str(outside_migration.count("secure_store.protect_secret")))


# =====================================================================
# 4. Скрипты переключения
# =====================================================================
def test_scripts() -> None:
    print("\n[Скрипты включения и отключения]")

    for name in ("Enable-PrivateMode.ps1", "Disable-PrivateMode.ps1"):
        path = INSTALL / name
        check(path.exists(), f"{name} на месте")
        if not path.exists():
            continue
        raw = path.read_bytes()
        # PowerShell 5.1 (штатный в Windows) читает кириллицу только с BOM
        check(raw.startswith(b"\xef\xbb\xbf"), f"{name}: есть BOM")
        src = raw.decode("utf-8")
        check(src.count("{") == src.count("}"), f"{name}: скобки сбалансированы")

    for name in ("enable-private-mode.bat", "disable-private-mode.bat"):
        path = INSTALL / name
        check(path.exists(), f"{name} на месте")
        if path.exists():
            try:
                path.read_bytes().decode("ascii")
                check(True, f"{name}: чистый ASCII (русский в .bat отображается неверно)")
            except UnicodeDecodeError:
                check(False, f"{name}: есть не-ASCII символы")

    enable = (INSTALL / "Enable-PrivateMode.ps1").read_text(encoding="utf-8-sig")
    check("PRIVATE_MODE = True" in enable, "Включение ставит PRIVATE_MODE = True")
    check("приватный" in enable.lower(), "Скрипт переспрашивает про приватность репозитория")
    check("Read-Host" in enable, "Есть подтверждение от пользователя")
    check("telegram_session" in enable,
          "Скрипт прямо говорит, что живые пропуска в git не пойдут")

    disable = (INSTALL / "Disable-PrivateMode.ps1").read_text(encoding="utf-8-sig")
    check("PRIVATE_MODE = False" in disable, "Отключение ставит PRIVATE_MODE = False")
    check("истори" in disable.lower(),
          "Отключение честно предупреждает: скрытие от будущих коммитов "
          "не убирает config.py из истории git")

    # Пометка, которой скрипт прячет правила, должна совпадать у обоих
    check("#ПРИВАТНЫЙ# " in enable and "#ПРИВАТНЫЙ# " in disable,
          "Включение и отключение используют одну и ту же пометку — иначе "
          "отменить режим не получится")


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ ПРИВАТНОГО РЕЖИМА")
    print("=" * 62)

    test_private_mode_flag()
    test_protect_secret()
    test_no_password_needed()
    test_gitignore_block()
    test_example_config_still_tracked()
    test_single_write_path()
    test_scripts()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
