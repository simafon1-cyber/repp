"""
accounts_backup.py — резервная копия списка торговых счетов в облаке.

ЗАЧЕМ
Список счетов (accounts.json) — единственное, что не переживает переустановку
или перенос на другой компьютер: файл специально остаётся вне git (в нём
логины и зашифрованные пароли брокера), поэтому обновление программы и
скачивание нового .exe его не трогают, а вот "снёс папку и поставил заново"
или "переехал на другой компьютер" — трогают. Этот модуль выкладывает файл в
папку backup/ вашего ЗАКРЫТОГО репозитория GitHub, откуда его можно вернуть
обратно кнопкой "Восстановить из облака".

ИСПОЛЬЗУЕТ ТЕ ЖЕ НАСТРОЙКИ, ЧТО ЖУРНАЛ СДЕЛОК
Один репозиторий, один токен — заводить для этого отдельные учётные данные
не нужно (см. cloud_journal.py, вкладка "Система" в интерфейсе).

БЕЗОПАСНОСТЬ ФАЙЛА, КОТОРЫЙ УХОДИТ В ОБЛАКО
Пароли счетов в accounts.json и так зашифрованы вашим паролем входа в
программу (Fernet + PBKDF2 200 000 итераций, см. secure_store.py и
accounts.py) — выгружается файл КАК ЕСТЬ, зашифрованным. Без пароля входа он
бесполезен, даже если репозиторий кто-то увидит. Тем не менее репозиторий
обязан быть ЗАКРЫТЫМ — это ответственность владельца, программа этого не
проверяет.

ЧТО НЕ ТЕРЯЕТСЯ ПРИ ВОССТАНОВЛЕНИИ
Восстановление никогда не стирает молча: если на диске уже есть accounts.json
(например, вы восстанавливаете НЕ на пустом месте), текущий файл сохраняется
рядом с припиской .before-restore, прежде чем на его место ляжет облачная
копия.
"""

from __future__ import annotations

import logging
import os
import sys
import time

import cloud_journal as cj
import safe_files

log = logging.getLogger("accounts_backup")

# Путь в репозитории — сознательно НЕ внутри cloud_journal.folder() (обычно
# "journal"): счета это не журнал сделок, смешивать их в одной папке
# означало бы, что человек, включивший только журнал, случайно решит, что
# бэкап счетов тоже включён (или наоборот).
BACKUP_PATH = "backup/accounts.json"


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def accounts_path() -> str:
    return os.path.join(app_dir(), "accounts.json")


def ready() -> tuple:
    """(готово, причина) — те же настройки, что и у журнала сделок."""
    return cj.ready()


def upload() -> dict:
    """Выложить accounts.json в облако как есть (уже зашифрован паролем
    входа). Возвращает {"ok", "error", "revision"}."""
    result = {"ok": False, "error": "", "revision": ""}
    ok, reason = ready()
    if not ok:
        result["error"] = reason
        return result

    path = accounts_path()
    if not os.path.exists(path):
        result["error"] = "Файл accounts.json не найден — счетов ещё нет."
        return result

    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        result["error"] = f"Не удалось прочитать accounts.json: {e}"
        return result

    message = f"Резервная копия счетов ({time.strftime('%d.%m.%Y %H:%M:%S')})"
    try:
        revision = cj.put_file(BACKUP_PATH, text, message)
    except Exception as e:  # noqa: BLE001
        result["error"] = cj.explain_error(e)
        log.warning("Резервная копия счетов не отправлена: %s", result["error"])
        return result

    result["ok"] = True
    result["revision"] = revision
    return result


def restore() -> dict:
    """Скачать accounts.json из облака и положить на место.

    Текущий локальный файл (если есть) не стирается молча — сохраняется
    рядом с припиской .before-restore. Возвращает {"ok", "error", "path"}."""
    result = {"ok": False, "error": "", "path": ""}
    ok, reason = ready()
    if not ok:
        result["error"] = reason
        return result

    try:
        text = cj.get_file(BACKUP_PATH)
    except Exception as e:  # noqa: BLE001
        result["error"] = cj.explain_error(e)
        return result
    if text is None:
        result["error"] = "В облаке ещё нет резервной копии счетов."
        return result

    path = accounts_path()
    if os.path.exists(path):
        backup_path = path + ".before-restore"
        try:
            with open(path, "r", encoding="utf-8") as f:
                current = f.read()
            with open(backup_path, "w", encoding="utf-8") as f:
                f.write(current)
        except OSError as e:
            result["error"] = f"Не удалось сохранить текущий файл перед заменой: {e}"
            return result

    try:
        safe_files.atomic_write_text(path, text)
        safe_files.restrict_to_current_user(path)
    except Exception as e:  # noqa: BLE001
        result["error"] = f"Не удалось записать accounts.json: {e}"
        return result

    result["ok"] = True
    result["path"] = path
    return result


def last_backup_info() -> dict:
    """Что сейчас лежит в облаке — не скачивая файл целиком, только когда
    обновлялся. {"exists", "error"}."""
    result = {"exists": False, "error": ""}
    ok, reason = ready()
    if not ok:
        result["error"] = reason
        return result
    try:
        sha = cj.remote_sha(BACKUP_PATH)
    except Exception as e:  # noqa: BLE001
        result["error"] = cj.explain_error(e)
        return result
    result["exists"] = bool(sha)
    return result
