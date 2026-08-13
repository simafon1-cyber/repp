"""
updater.py — обновление программы из GitHub без переустановки.

ЗАЧЕМ
Вы правите код, он уезжает на GitHub, а на рабочем компьютере программа
остаётся старой. Переустанавливать её каждый раз незачем — обновление
скачивается и применяется само.

ЧТО ОБНОВЛЯЕТСЯ, ОДНОЙ КНОПКОЙ «ОБНОВИТЬ ВСЁ»
  1. Советники и сервис календаря (.mq5/.mqh) — скачиваются, кладутся в
     каждый найденный терминал и компилируются. Перезапуск не нужен: это
     обычные файлы, которые читает MetaTrader, а не часть процесса.
  2. Сама программа:
       * запущена из исходников — скачиваются все .py и заменяются на месте;
       * запущена как .exe — скачивается готовый .exe из Releases (а если
         релиза нет — из последней сборки GitHub Actions).
  3. Новые настройки — дописываются в config.py (config_migrate.py).

ЧТО ТРЕБУЕТ ПЕРЕЗАПУСКА
Только сама программа. Работающий файл заменить нельзя — Windows его держит,
а у Python уже загружены модули. Поэтому новая версия кладётся рядом, а
подмена происходит в самом начале следующего запуска (apply_pending_swap).

ЕСЛИ ГОТОВОЙ СБОРКИ ЕЩЁ НЕТ
.exe собирается на серверах GitHub. Раньше это приходилось запускать руками
через вкладку Actions. Теперь программа умеет попросить сборку сама
(request_build) и потом забрать результат — руками ничего открывать не надо.

ЧТО НИКОГДА НЕ ПЕРЕЗАПИСЫВАЕТСЯ
config.py (ваши настройки и ключи), accounts.json, telegram_session, журналы,
trades_log.csv, learning_state.json. Список — в PROTECTED. Обновление,
затирающее ваши пароли, — это не обновление, а потеря данных.

ВСЁ ИЛИ НИЧЕГО
Файлы сначала скачиваются во временную папку и проверяются (каждый .py должен
разбираться как Python). Только если проверку прошли ВСЕ — они заменяют
рабочие, и то с резервной копией. Половина новых файлов и половина старых —
верный способ получить программу, которая не запускается.

ПРИВАТНЫЙ РЕПОЗИТОРИЙ
Нужен токен GitHub (Settings -> Developer settings -> Personal access tokens
-> Fine-grained, ваш репозиторий). Права: Contents: Read-only — для обычного
обновления; Actions: Read and write — если хотите, чтобы программа сама
заказывала сборку .exe. Токен — такой же секрет, как ключи API: хранится в
config.py и шифруется наравне с ними.

ЧЕГО ЗДЕСЬ НЕТ
Обновление не ставится молча ПОСРЕДИ РАБОТЫ. Автоматически оно применяется
только при запуске программы, до начала торговли (UPDATE_AUTO_APPLY). Если
новая версия появилась, когда открыты позиции, программа скажет об этом и
будет ждать решения: подменять торгового робота под открытыми сделками
нельзя.
"""

import ast
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import config as cfg
import secure_store
import version as app_version

log = logging.getLogger("updater")

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"

# Файлы, которые обновление НЕ ТРОГАЕТ никогда. Это ваши данные, а не код.
PROTECTED = {
    "config.py",
    "accounts.json",
    "accounts.tmp",
    "telegram_session",
    "trades_log.csv",
    "trades_log.csv.sha256",
    "learning_state.json",
    ".login_remember",
    "scalper.log",
    "config.py.sha256",
}

# Папка репозитория, в которой лежит сама программа.
PROGRAM_DIR = "ai_scalper_standalone"

# Файлы советников, которые имеет смысл обновлять отдельно от программы.
# Совпадает со списком в mt5_install — там же они и ставятся.
MQL_FILES = [
    ("mql5", "CalendarExport.mq5"),
    ("mql5", "DualGuardEA.mq5"),
    ("ai_scalper_pro", "AI_Scalper_Pro.mq5"),
    ("ai_scalper_pro", "Config.mqh"),
    ("ai_scalper_pro", "CustomStrategy.mqh"),
    ("ai_scalper_pro", "Dashboard.mqh"),
    ("ai_scalper_pro", "Indicators.mqh"),
    ("ai_scalper_pro", "MarketContext.mqh"),
    ("ai_scalper_pro", "MarketRegime.mqh"),
    ("ai_scalper_pro", "MultiIndicator.mqh"),
    ("ai_scalper_pro", "NewsAI.mqh"),
    ("ai_scalper_pro", "RiskManager.mqh"),
    ("ai_scalper_pro", "SignalEngine.mqh"),
    ("ai_scalper_pro", "TradeManager.mqh"),
]


def enabled() -> bool:
    return bool(getattr(cfg, "UPDATE_ENABLED", False))


def repo() -> str:
    """owner/repo, например simafon1-cyber/repp."""
    return str(getattr(cfg, "UPDATE_REPO", "") or "").strip().strip("/")


_default_branch_cache = {}


def repo_default_branch() -> str:
    """Ветка по умолчанию у репозитория на GitHub (то, что там открывается
    первым).

    Раньше пустое поле «Ветка» вслепую подставляло "main". Для репозитория,
    где ветка main не заводилась вовсе (обычное дело, пока никто не сделал
    первый Pull Request) — это означало «Репозиторий или ветка не найдены»
    буквально на КАЖДЫЙ файл, хотя сам репозиторий и рабочая ветка в нём
    существуют. Теперь при пустом поле программа спрашивает у самого GitHub,
    какая ветка в этом репозитории главная."""
    key = repo()
    if key in _default_branch_cache:
        return _default_branch_cache[key]
    value = "main"
    try:
        with _request(f"{API}/repos/{key}") as response:
            data = json.loads(response.read().decode("utf-8"))
        value = str(data.get("default_branch", "") or "main")
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось узнать ветку по умолчанию репозитория %s: %s", key, e)
    _default_branch_cache[key] = value
    return value


# Ветка, которой программа ПОЛЬЗУЕТСЯ вместо вписанной, потому что вписанной
# в репозитории нет (см. recover_branch). Ключ — репозиторий.
_branch_override = {}


def branch() -> str:
    """Ветка, из которой берутся файлы.

    Сети здесь не касаемся, если ветка вписана руками: обновление дёргает
    branch() на каждый файл, лишний запрос к GitHub на каждом — это и
    медленно, и упирается в ограничение числа запросов."""
    if repo() in _branch_override:
        return _branch_override[repo()]
    configured = str(getattr(cfg, "UPDATE_BRANCH", "") or "").strip()
    return configured or repo_default_branch()


def list_branches() -> list:
    """Имена веток репозитория. Пустой список — спросить не удалось."""
    try:
        with _request(f"{API}/repos/{repo()}/branches?per_page=100") as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось получить список веток %s: %s", repo(), e)
        return []
    if not isinstance(data, list):
        return []
    return [str(item.get("name", "")) for item in data if item.get("name")]


def best_branch_match(wanted: str, names: list, default: str = "") -> str:
    """Какая из существующих веток имелась в виду.

    Живой случай: поле «Ветка» на экране было узким, в него влезало
    «claude/metatrader5-trading», и человек честно считал, что вписал имя
    целиком. GitHub на обрезанное имя отвечает 404 на КАЖДЫЙ файл — и это
    выглядит как «нет доступа к репозиторию», хотя доступ есть.

    Порядок: точное совпадение -> без учёта регистра -> ветка, чьё имя
    НАЧИНАЕТСЯ с вписанного (обрезали) -> ветка по умолчанию. Из нескольких
    подходящих берём самую короткую: у неё меньше всего лишнего сверх того,
    что человек вписал."""
    names = [n for n in names or [] if n]
    if not names:
        return ""
    wanted = (wanted or "").strip()
    if wanted in names:
        return wanted
    if wanted:
        lowered = [n for n in names if n.lower() == wanted.lower()]
        if lowered:
            return min(lowered, key=len)
        prefixed = [n for n in names if n.startswith(wanted)]
        if prefixed:
            return min(prefixed, key=len)
    if default in names:
        return default
    return ""


def recover_branch() -> str:
    """Починить ветку, если вписанной в репозитории нет. Возвращает пояснение
    (пустая строка — чинить нечего или не вышло).

    Вызывается ТОЛЬКО после отказа 404, а не заранее: пока всё скачивается,
    лишних запросов к GitHub быть не должно."""
    if repo() in _branch_override:
        return ""   # уже чинили в этом сеансе
    current = branch()
    names = list_branches()
    if not names or current in names:
        return ""
    fixed = best_branch_match(current, names, repo_default_branch())
    if not fixed or fixed == current:
        return ""
    _branch_override[repo()] = fixed
    note = (f"Ветки «{current}» в репозитории нет — беру «{fixed}». "
            f"Впишите её в поле «Ветка» и нажмите «Сохранить» "
            f"(или очистите поле совсем — тогда программа всегда берёт "
            f"главную ветку репозитория сама).")
    log.warning("%s", note)
    return note


def branch_was_fixed() -> str:
    """Какой веткой программа пользуется вместо вписанной (для интерфейса)."""
    return _branch_override.get(repo(), "")


def reset_caches() -> None:
    """Забыть всё, что зависит от настроек репозитория.

    Вызывается после правки настроек обновления: иначе программа продолжила бы
    пользоваться веткой по умолчанию, определённой для ПРЕЖНЕГО репозитория, и
    отказ выглядел бы необъяснимо."""
    _default_branch_cache.clear()
    _branch_override.clear()
    _token_ignored["value"] = False


def token_locked() -> bool:
    """Токен сохранён, но в этой сессии не расшифрован (программа открылась
    без пароля входа). Пользоваться им нельзя — см. secure_store.is_locked."""
    return secure_store.is_locked(
        str(getattr(cfg, "UPDATE_TOKEN", "") or "").strip())


def token() -> str:
    """Токен для GitHub — только если им действительно можно пользоваться.

    Зашифрованную строку сюда пропускать нельзя: она уходила в заголовок
    Authorization, GitHub отвечал 401, а человек читал «нужен токен GitHub» —
    хотя токен у него как раз был. Пустая строка честнее: без заголовка
    ОТКРЫТЫЙ репозиторий читается прекрасно, а для закрытого мы отдельно
    объясним настоящую причину (см. auth_hint)."""
    raw = str(getattr(cfg, "UPDATE_TOKEN", "") or "").strip()
    if secure_store.is_locked(raw):
        return ""
    return raw


# Токен был, но GitHub его не принял, а без токена всё получилось — значит
# репозиторий открытый и токен вообще не нужен. Показываем это в интерфейсе:
# молча игнорировать настройку пользователя нельзя.
_token_ignored = {"value": False}


def token_was_ignored() -> bool:
    return _token_ignored["value"]


def auth_hint() -> str:
    """Настоящая причина отказа по правам — словами, а не «нужен токен»."""
    if token_locked():
        return ("Токен GitHub сохранён, но не расшифрован: программа открылась "
                "без пароля входа. Либо войдите с паролем, либо впишите токен "
                "заново на вкладке «Система» и нажмите «Сохранить».")
    if not token():
        return ("Токен GitHub не задан. Для ОТКРЫТОГО репозитория он и не "
                "нужен; для закрытого — обязателен (права Contents: Read-only).")
    return ("Токен GitHub не принят: возможно, истёк, отозван или выдан не на "
            "этот репозиторий.")


def current_version() -> str:
    """Что установлено сейчас. Пишется при удачном обновлении."""
    return str(getattr(cfg, "UPDATE_INSTALLED_REV", "") or "")


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _open(url: str, accept: str, timeout: int, use_token: bool):
    headers = {"Accept": accept, "User-Agent": "AI-Scalper-Updater"}
    if use_token and token():
        headers["Authorization"] = f"Bearer {token()}"
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout)


def _request(url: str, accept: str = "application/vnd.github+json", timeout: int = 20):
    """Запрос к GitHub с токеном, если он задан и пригоден.

    Если токен есть, но GitHub его не принял (401/403) — пробуем ТОТ ЖЕ запрос
    без токена. Для открытого репозитория это срабатывает: чтение публичных
    файлов не требует никаких прав, а неверный заголовок Authorization ломает
    даже их. Без этого одна испорченная настройка (истёкший токен, опечатка,
    нерасшифрованная строка) намертво останавливала обновление программы,
    которому токен вообще не был нужен. Если и без токена отказ — репозиторий
    действительно закрытый, отдаём исходную ошибку про права."""
    try:
        return _open(url, accept, timeout, use_token=True)
    except urllib.error.HTTPError as e:
        if e.code not in (401, 403) or not token():
            raise
        try:
            response = _open(url, accept, timeout, use_token=False)
        except urllib.error.HTTPError:
            raise e
        _token_ignored["value"] = True
        log.warning("GitHub не принял токен, но репозиторий открывается и без "
                    "него — продолжаю без токена (%s)", url)
        return response


def explain_error(exc: Exception) -> str:
    """Ошибку сети — понятной фразой."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            # Общими словами («проверьте UPDATE_REPO и UPDATE_BRANCH») эту
            # ошибку починить нельзя: не видно, ЧЕМ именно программа сейчас
            # пользуется. Называем ветку и перечисляем существующие — тогда
            # опечатка или обрезанное имя видны с первого взгляда.
            existing = list_branches()
            if existing:
                names = ", ".join(f"«{n}»" for n in existing[:10])
                return (f"В репозитории «{repo()}» нет ветки «{branch()}». "
                        f"Есть такие ветки: {names}. Впишите нужную в поле "
                        f"«Ветка» и нажмите «Сохранить» — или очистите поле "
                        f"совсем, тогда программа всегда возьмёт главную ветку "
                        f"сама.")
            return (f"Репозиторий «{repo()}» или ветка «{branch()}» не найдены. "
                    "Проверьте название репозитория (владелец/название) и "
                    "ветку — оставьте поле «Ветка» пустым, чтобы программа "
                    "сама взяла главную ветку. Для закрытого репозитория "
                    "нужен токен.")
        if exc.code in (401, 403):
            return "Нет доступа к репозиторию. " + auth_hint()
        if exc.code == 429:
            return "GitHub временно ограничил число запросов. Попробуйте позже."
        return f"GitHub ответил ошибкой {exc.code}."
    if isinstance(exc, urllib.error.URLError):
        return f"Нет связи с GitHub: {exc.reason}"
    return f"{type(exc).__name__}: {exc}"


# =====================================================================
# ПРОВЕРКА
# =====================================================================
def check() -> dict:
    """Есть ли что-то новее установленного.

    Возвращает {"available", "revision", "message", "error"}."""
    result = {"available": False, "revision": "", "message": "", "error": ""}

    if not enabled():
        result["error"] = "Обновление выключено в настройках (UPDATE_ENABLED = False)."
        return result
    if not repo() or "/" not in repo():
        result["error"] = ("Не задан репозиторий. Впишите его в виде "
                           "владелец/название на вкладке «Обновление».")
        return result

    def fetch():
        url = f"{API}/repos/{repo()}/commits/{branch()}"
        with _request(url) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        data = fetch()
    except urllib.error.HTTPError as e:
        # Ветки нет — подобрать существующую и повторить (см. recover_branch)
        try:
            fixed = recover_branch() if e.code == 404 else ""
            data = fetch() if fixed else None
        except Exception as retry_error:  # noqa: BLE001
            result["error"] = explain_error(retry_error)
            return result
        if data is None:
            result["error"] = explain_error(e)
            return result
    except Exception as e:  # noqa: BLE001
        result["error"] = explain_error(e)
        return result

    revision = str(data.get("sha", ""))[:12]
    if not revision:
        result["error"] = "GitHub вернул ответ без номера версии."
        return result

    result["revision"] = revision
    commit = (data.get("commit") or {}).get("message", "").splitlines()
    result["message"] = commit[0][:120] if commit else ""

    if revision == current_version():
        result["message"] = "Установлена последняя версия."
        return result

    result["available"] = True
    return result


# =====================================================================
# ПРИМЕНЕНИЕ
# =====================================================================
def download_text(path: str) -> str:
    """Один файл из репозитория как текст.

    На 404 один раз пробуем починить ветку (recover_branch) и повторяем. Без
    этого одна опечатка или обрезанное имя ветки давала «Репозиторий или
    ветка не найдены» на каждый файл подряд, и починить это можно было
    только вручную — а инструкция «исправьте ветку» лежала внутри того
    самого обновления, которое не скачивалось."""
    def fetch():
        url = f"{RAW}/{repo()}/{branch()}/{path}"
        with _request(url, accept="text/plain") as response:
            return response.read().decode("utf-8", errors="replace")

    try:
        return fetch()
    except urllib.error.HTTPError as e:
        if e.code != 404 or not recover_branch():
            raise
        return fetch()


def update_advisors(progress=None) -> dict:
    """Скачивает свежие .mq5/.mqh и ставит их в терминалы.

    Перезапуск программы для этого не нужен: это обычные файлы, которые
    читает MetaTrader, а не часть работающего процесса."""
    def say(text):
        if progress:
            try:
                progress(text)
            except Exception:
                pass

    report = {"downloaded": 0, "errors": [], "installed": ""}

    staging = tempfile.mkdtemp(prefix="ai-scalper-update-")
    for subdir, name in MQL_FILES:
        say(f"Скачиваю {name}...")
        try:
            text = download_text(f"{subdir}/{name}")
        except Exception as e:
            report["errors"].append(f"{name}: {explain_error(e)}")
            continue
        target_dir = os.path.join(staging, subdir)
        os.makedirs(target_dir, exist_ok=True)
        with open(os.path.join(target_dir, name), "w", encoding="utf-8") as f:
            f.write(text)
        report["downloaded"] += 1

    if not report["downloaded"]:
        report["errors"].append("Не удалось скачать ни одного файла — обновление отменено.")
        return report

    # Ставим ТОЛЬКО если скачалось всё: половина новых файлов и половина
    # старых — верный способ получить советник, который не собирается.
    if len(report["errors"]) > 0:
        report["errors"].append(
            "Скачалось не всё — установка отменена, чтобы не смешать версии.")
        return report

    say("Ставлю в MetaTrader...")
    try:
        import mt5_install
        saved_root = mt5_install.bundled_root
        mt5_install.bundled_root = lambda: staging
        try:
            install_report = mt5_install.install_all(progress=progress)
        finally:
            mt5_install.bundled_root = saved_root
        report["installed"] = install_report.get("text", "")
        report["errors"].extend(install_report.get("errors", []))
    except Exception as e:
        report["errors"].append(f"Установка не удалась: {e}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return report


# =====================================================================
# ОБНОВЛЕНИЕ САМОЙ ПРОГРАММЫ: ЗАПУСК ИЗ ИСХОДНИКОВ
# =====================================================================
def is_frozen() -> bool:
    """Программа запущена как собранный .exe (а не как набор .py)."""
    return bool(getattr(sys, "frozen", False))


def list_repo_files() -> list:
    """Все файлы репозитория одним запросом (дерево целиком).

    Список файлов НЕ зашит в код намеренно: когда в программе появляется
    новый модуль, зашитый список о нём не знает — обновление молча привезло
    бы половину новой версии."""
    def fetch():
        url = f"{API}/repos/{repo()}/git/trees/{branch()}?recursive=1"
        with _request(url) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        data = fetch()
    except urllib.error.HTTPError as e:
        if e.code != 404 or not recover_branch():
            raise
        data = fetch()
    if data.get("truncated"):
        log.warning("GitHub отдал дерево файлов не целиком — репозиторий очень большой.")
    return [item["path"] for item in (data.get("tree") or [])
            if item.get("type") == "blob"]


def safe_relative(path: str) -> bool:
    """Путь из ответа GitHub можно безопасно превратить в путь на диске.

    Ответ сети — не доверенные данные: путь вида ../../Windows/System32
    вывел бы запись за пределы папки программы. Проверяем до, а не после."""
    if not path or path.startswith("/") or path.startswith("\\"):
        return False
    if ":" in path:
        return False
    parts = path.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    return True


def program_files(all_paths=None) -> list:
    """Какие файлы программы обновлять. Возвращает список (путь_в_репозитории,
    имя_на_диске) — программа лежит в одной папке, вложенности у неё нет."""
    if all_paths is None:
        all_paths = list_repo_files()
    prefix = PROGRAM_DIR + "/"
    out = []
    for path in all_paths:
        if not path.startswith(prefix) or not safe_relative(path):
            continue
        name = path[len(prefix):]
        if "/" in name:
            continue                      # вложенных папок у программы нет
        if name in PROTECTED:
            continue                      # ваши данные не трогаем
        if not (name.endswith(".py") or name == "config.py.example"
                or name in ("requirements.txt", "requirements-build.txt")):
            continue
        out.append((path, name))
    return sorted(out)


def update_program_files(progress=None) -> dict:
    """Обновляет .py самой программы (для запуска из исходников).

    Всё или ничего: сначала скачиваем во временную папку и проверяем, что
    каждый файл разбирается как Python, и только потом заменяем рабочие."""
    def say(text):
        if progress:
            try:
                progress(text)
            except Exception:
                pass

    report = {"downloaded": 0, "replaced": 0, "errors": [], "restart_needed": False}

    try:
        files = program_files()
    except Exception as e:
        report["errors"].append(f"Не удалось получить список файлов: {explain_error(e)}")
        return report
    if not files:
        report["errors"].append(
            f"В репозитории не найдена папка {PROGRAM_DIR}/ — проверьте название "
            f"репозитория и ветку.")
        return report

    staging = tempfile.mkdtemp(prefix="ai-scalper-src-")
    try:
        for path, name in files:
            say(f"Скачиваю {name}...")
            try:
                text = download_text(path)
            except Exception as e:
                report["errors"].append(f"{name}: {explain_error(e)}")
                break
            if name.endswith(".py"):
                try:
                    ast.parse(text)
                except SyntaxError as e:
                    report["errors"].append(f"{name}: скачанный файл битый ({e})")
                    break
            with open(os.path.join(staging, name), "w", encoding="utf-8",
                      newline="\n") as f:
                f.write(text)
            report["downloaded"] += 1

        if report["errors"]:
            report["errors"].append(
                "Скачалось не всё — ничего не заменено, чтобы не смешать версии.")
            return report

        say("Заменяю файлы...")
        target_dir = app_dir()
        for _, name in files:
            source = os.path.join(staging, name)
            destination = os.path.join(target_dir, name)
            try:
                new_text = open(source, encoding="utf-8").read()
                if os.path.exists(destination):
                    old_text = open(destination, encoding="utf-8", errors="replace").read()
                    if old_text == new_text:
                        continue          # не трогаем то, что и так свежее
                    shutil.copy2(destination, destination + ".bak")
                shutil.copy2(source, destination)
                report["replaced"] += 1
            except OSError as e:
                report["errors"].append(f"{name}: не удалось записать ({e})")
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    report["restart_needed"] = report["replaced"] > 0
    return report


# =====================================================================
# ОБНОВЛЕНИЕ САМОЙ ПРОГРАММЫ: СОБРАННЫЙ .EXE
# =====================================================================
EXE_NAME = "AI_Scalper_Pro.exe"
INSTALLER_NAME = "AI_Scalper_Setup.exe"
BUILD_WORKFLOW = "build-exe.yml"
ARTIFACT_NAME = "AI_Scalper_Pro-windows"

# Папка, которую PyInstaller кладёт рядом с программой при установке ПАПКОЙ.
# Внутри лежит сам Python и все библиотеки.
ВНУТРЕННЯЯ_ПАПКА = "_internal"


def installed_as_folder() -> bool:
    """Программа установлена ПАПКОЙ (через установщик), а не одним файлом.

    ЗАЧЕМ ЭТО РАЗЛИЧАТЬ. Программа существует в двух видах, и они устроены
    по-разному внутри:

      * ОДНИМ ФАЙЛОМ — весь Python спрятан внутрь .exe и при каждом запуске
        распаковывается во временную папку. Отсюда прежние ошибки про
        init.tcl, base_library.zip и «Failed to remove temporary directory».
      * ПАПКОЙ — Python лежит рядом, в подпапке _internal, и не
        распаковывается вовсе. Так ставит установщик, и так быстрее.

    Самообновление умело только одно: скачать .exe и подменить им работающий.
    Для установки ПАПКОЙ это неверно: подменив .exe, мы оставляем рядом чужую
    папку _internal от прежней версии и молча превращаем быструю установку
    обратно в медленную, со всеми её ошибками распаковки. Мешать два вида
    установки в одной папке нельзя."""
    if not getattr(sys, "frozen", False):
        return False
    рядом = os.path.abspath(os.path.dirname(sys.executable))
    распаковано = getattr(sys, "_MEIPASS", "")
    if распаковано:
        # Одним файлом — распаковка уходит во временную папку, далеко от
        # программы. Папкой — «распакованное» лежит внутри самой установки.
        try:
            return os.path.commonpath(
                [os.path.abspath(распаковано), рядом]) == рядом
        except ValueError:      # разные диски — точно не наша папка
            return False
    return os.path.isdir(os.path.join(рядом, ВНУТРЕННЯЯ_ПАПКА))


def installer_advice() -> str:
    """Что делать человеку, когда обновиться подменой файла нельзя.

    Текст один на все места, чтобы не расходился, и со ссылкой: без неё
    совет «скачайте установщик» упирается в вопрос «откуда?»."""
    try:
        ссылка = f"https://github.com/{repo()}/releases/latest"
    except Exception:  # noqa: BLE001
        ссылка = ""
    совет = ("Программа установлена папкой, и подменять в ней один файл "
             "нельзя: рядом останется Python от прежней версии, и программа "
             f"перестанет запускаться. Скачайте {INSTALLER_NAME}, закройте "
             "программу и запустите установщик — он обновит всё разом и "
             "сохранит ваши настройки.")
    return совет + (f" Страница загрузки: {ссылка}" if ссылка else "")


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Убирает заголовок Authorization при переходе на ЧУЖОЙ адрес.

    GitHub отдаёт файлы релизов и артефактов не сам: он отвечает
    перенаправлением на подписанную ссылку в хранилище (amazonaws.com).
    urllib по умолчанию тащит все заголовки за собой — хранилище видит чужой
    заголовок Authorization, не понимает его и отвечает отказом. Плюс это
    означало бы отправку вашего токена GitHub на сторонний сервер, чего быть
    не должно."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        old_host = urllib.parse.urlparse(req.full_url).netloc
        new_host = urllib.parse.urlparse(newurl).netloc
        if old_host != new_host:
            for name in list(new_req.headers):
                if name.lower() == "authorization":
                    del new_req.headers[name]
            new_req.unredirected_hdrs.pop("Authorization", None)
        return new_req


def _open_binary_once(url: str, accept: str, timeout: int, use_token: bool):
    headers = {"Accept": accept, "User-Agent": "AI-Scalper-Updater"}
    if use_token and token():
        headers["Authorization"] = f"Bearer {token()}"
    opener = urllib.request.build_opener(_DropAuthOnRedirect)
    return opener.open(urllib.request.Request(url, headers=headers), timeout=timeout)


def _open_binary(url: str, accept: str, timeout: int = 300):
    """Скачивание файла. Тот же запасной путь, что и у _request: непринятый
    токен не должен мешать скачать то, что и так лежит открыто."""
    try:
        return _open_binary_once(url, accept, timeout, use_token=True)
    except urllib.error.HTTPError as e:
        if e.code not in (401, 403) or not token():
            raise
        try:
            response = _open_binary_once(url, accept, timeout, use_token=False)
        except urllib.error.HTTPError:
            raise e
        _token_ignored["value"] = True
        log.warning("GitHub не принял токен при скачивании — продолжаю без него (%s)", url)
        return response


# Меньше этого рабочей программой файл быть не может: одна только упаковка
# Python с библиотеками весит десятки мегабайт. Если скачалось меньше — это
# обрывок, а не программа.
MIN_EXE_BYTES = 5 * 1024 * 1024


class TruncatedDownload(Exception):
    """Файл скачался не полностью. Отдельный тип — чтобы не спутать с сетевой
    ошибкой: сеть можно повторить, а обрывок нельзя ставить НИКОГДА."""


def download_binary(url: str, destination: str, accept: str,
                    progress=None) -> int:
    """Скачивает большой файл кусками и ПРОВЕРЯЕТ, что он дошёл целиком.

    ПОЧЕМУ ПРОВЕРКА ЗДЕСЬ ГЛАВНОЕ. Раньше цикл просто заканчивался, когда
    поток переставал отдавать данные, — а обрыв связи выглядит точно так же.
    Обрезанный файл молча вставал на место рабочей программы, и она переставала
    запускаться: у собранного .exe внутри упакованы библиотеки, и на обрубке
    распаковка падает с невнятной ошибкой вроде «Can't find a usable init.tcl».
    Именно это и увидел владелец.

    Возвращает число скачанных байт. Не сошлось с обещанным — бросаем
    TruncatedDownload, и вызывающий обязан НЕ ставить такой файл."""
    with _open_binary(url, accept=accept, timeout=300) as response:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with open(destination, "wb") as f:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    try:
                        progress(f"Скачиваю программу: {done * 100 // total}%")
                    except Exception:
                        pass

    if total and done != total:
        raise TruncatedDownload(
            f"файл скачался не полностью: {done} байт из {total}")
    return done


# Опознавательная метка упаковщика PyInstaller. Она лежит в САМОМ КОНЦЕ
# собранного файла — то есть появляется только если файл дошёл до последнего
# байта. Именно этим она и ценна: обрезанную закачку видно точно, а не на глаз
# по размеру.
#
# ЧЕМ РИСКУЕМ. Если однажды упаковщик сменит метку, обновление начнёт
# отказываться ставить ИСПРАВНЫЕ файлы. Это неприятно, но несравнимо лучше
# обратной ошибки: там человек остаётся с неработающей программой и без
# способа её вернуть. Отказ виден сразу и чинится вручную за минуту.
PYINSTALLER_MAGIC = b"MEI\014\013\012\013\016"

# Windows-программа всегда начинается с этих двух букв.
EXE_HEADER = b"MZ"


def looks_like_program(path: str) -> str:
    """Похоже ли скачанное на рабочую программу. Пусто — похоже.

    ЗАЧЕМ ТРИ РАЗНЫЕ ПРОВЕРКИ. Владелец дважды получал неработающую программу
    после обновления: сперва «Can't find a usable init.tcl», потом «No such
    file or directory: ...base_library.zip». Обе ошибки — об одном: файл
    оборвался, и упаковщик не смог достать из него своё содержимое.

    Размер ловит грубый обрыв. Заголовок ловит случай, когда вместо программы
    прислали страницу с ошибкой. Метка в конце файла ловит самое коварное:
    файл почти целый, размер правдоподобный, а последних байт нет."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return f"скачанный файл не читается ({e})"
    if size < MIN_EXE_BYTES:
        return (f"скачано всего {size / 1024 / 1024:.1f} МБ — это обрывок, "
                f"а не программа (рабочая версия весит десятки мегабайт)")

    try:
        with open(path, "rb") as f:
            head = f.read(2)
            # Метка лежит в самом конце; читаем ТОЛЬКО последний кусок.
            # Ограничение существенно: файл весит десятки мегабайт, и читать
            # его целиком ради восьми байт в конце — тянуть всё это в память
            # без всякой нужды.
            window = 8192
            f.seek(max(0, size - window))
            tail = f.read(window)
    except OSError as e:
        return f"скачанный файл не читается ({e})"

    if head != EXE_HEADER:
        return ("скачано не приложение Windows — похоже, вместо программы "
                "пришла страница с ошибкой")
    if PYINSTALLER_MAGIC not in tail:
        return ("файл оборван: не хватает конца — ровно из-за этого программа "
                "потом не запускается с ошибкой про init.tcl или base_library")
    return ""


def latest_release_exe() -> dict:
    """Ссылка на .exe из последнего релиза. {"url", "name", "tag"} или {}."""
    url = f"{API}/repos/{repo()}/releases/latest"
    try:
        with _request(url) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}                     # релизов ещё нет — это не ошибка
        raise
    # ИМЕННО ПО ИМЕНИ, а не «первый попавшийся .exe». В релизе теперь лежат
    # ДВА исполняемых файла: установщик AI_Scalper_Setup.exe и сама программа
    # одним файлом. Прежнее правило «первый .exe» подменило бы работающую
    # программу установщиком — и она перестала бы запускаться вовсе.
    for asset in (data.get("assets") or []):
        if str(asset.get("name", "")).lower() == EXE_NAME.lower():
            # size — сколько байт у файла НА САМОМ ДЕЛЕ, по данным GitHub.
            # Это независимая мерка: Content-Length приходит вместе с самой
            # закачкой и через посредника может прийти уже подогнанным под
            # обрезанный ответ, а размер из описания релиза берётся отдельным
            # запросом и такому не подвержен.
            return {"url": asset.get("url", ""), "name": asset.get("name", ""),
                    "size": int(asset.get("size") or 0),
                    "tag": str(data.get("tag_name", ""))}
    return {}


def latest_release_build() -> int:
    """Номер последней выпущенной сборки на GitHub. 0 — узнать не удалось.

    Релизы обычных сборок называются build-<номер> (см. build-exe.yml).
    Разбираем именно имя тега: сравнивать номера сборок человеку проще и
    понятнее, чем номера правок."""
    try:
        with _request(f"{API}/repos/{repo()}/releases/latest") as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    tag = str(data.get("tag_name", "") or "")
    match = re.search(r"(\d+)", tag)
    return int(match.group(1)) if match else 0


def version_status() -> str:
    """Одной строкой: какая версия установлена и есть ли новее.

    Владелец: «прописывай где-то версию, чтобы было видно, обновилось или
    нет». Номера сборки самого по себе мало — важно, ПОСЛЕДНЯЯ ли она.
    Несколько раз выходило так, что исправление давно выпущено, а запущена
    старая версия, и мы искали ошибку, которой в новой уже нет."""
    mine = app_version.full()
    if not app_version.is_release():
        return f"Версия: {mine}"
    installed = app_version.number()
    latest = latest_release_build()
    if latest <= 0:
        return f"Версия: {mine} (узнать про новые не удалось)"
    if latest > installed:
        return (f"Версия: {mine}. На GitHub есть новее — сборка {latest}. "
                f"Нажмите «Обновить всё сейчас».")
    return f"Версия: {mine} — это последняя, обновлять нечего."


def latest_build_artifact() -> dict:
    """Последняя УСПЕШНАЯ сборка .exe из GitHub Actions.

    Нужна, когда релиз ещё не выпущен: сборка происходит на каждый запуск
    workflow, а релиз — только по тегу. Артефакты живут ограниченное время и
    скачиваются zip-ом, поэтому это запасной путь, а не основной."""
    url = f"{API}/repos/{repo()}/actions/artifacts?per_page=30"
    with _request(url) as response:
        data = json.loads(response.read().decode("utf-8"))
    for item in (data.get("artifacts") or []):
        if item.get("expired"):
            continue
        # Именно НАША сборка: в репозитории могут накапливаться артефакты
        # других сценариев, и взять первый попавшийся значило бы скачать
        # неизвестно что и подсунуть это вместо программы.
        if str(item.get("name", "")) != ARTIFACT_NAME:
            continue
        return {"url": item.get("archive_download_url", ""),
                "name": str(item.get("name", "")),
                "created": str(item.get("created_at", ""))[:10]}
    return {}


def _extract_exe(zip_path: str, destination: str) -> bool:
    """Достаёт программу из скачанного архива сборки.

    В архиве лежит не только она: рядом установщик AI_Scalper_Setup.exe. Брать
    «первый попавшийся .exe» нельзя — вместо программы подменился бы
    установщик, и при следующем запуске открылось бы окно установки."""
    with zipfile.ZipFile(zip_path) as archive:
        # Имена внутри архива — тоже не доверенные данные (ZipSlip).
        members = [m for m in archive.namelist()
                   if m.lower().endswith(".exe") and safe_relative(m)]
        exact = [m for m in members
                 if m.replace("\\", "/").split("/")[-1].lower() == EXE_NAME.lower()]
        chosen = exact[0] if exact else None
        if chosen is None:
            # Запасной путь: любой .exe, кроме установщика.
            rest = [m for m in members if "setup" not in m.lower()]
            chosen = rest[0] if rest else None
        if chosen is None:
            return False
        with archive.open(chosen) as src, open(destination, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return True


def download_new_exe(progress=None) -> dict:
    """Кладёт новую версию программы рядом со старой (файл .new).

    Подмена произойдёт при следующем запуске — работающий .exe заменить
    нельзя. Возвращает {"ok", "source", "error"}."""
    def say(text):
        if progress:
            try:
                progress(text)
            except Exception:
                pass

    result = {"ok": False, "source": "", "error": ""}

    # НЕ КАЧАЕМ ТО, ЧЕГО ВСЁ РАВНО НЕ ПОСТАВИМ. Установка папкой обновляется
    # установщиком целиком; скачать 60 МБ и потом отказаться — значит зря
    # потратить связь человека и его время.
    if installed_as_folder():
        result["error"] = installer_advice()
        return result

    destination = pending_swap_path()
    temporary = destination + ".part"

    try:
        say("Ищу готовую сборку...")
        release = latest_release_exe()
        if release.get("url"):
            say(f"Скачиваю версию {release.get('tag', '')}...")
            got = download_binary(release["url"], temporary,
                                  accept="application/octet-stream",
                                  progress=progress)
            expected = int(release.get("size") or 0)
            if expected and got != expected:
                result["error"] = (
                    f"Обновление не установлено: GitHub сообщает, что файл "
                    f"весит {expected} байт, а скачалось {got}. "
                    f"Прежняя версия осталась на месте — попробуйте ещё раз "
                    f"при устойчивой связи.")
                return result
            broken = looks_like_program(temporary)
            if broken:
                result["error"] = ("Обновление не установлено: " + broken +
                                   ". Прежняя версия осталась на месте — "
                                   "попробуйте ещё раз при устойчивой связи.")
                return result
            os.replace(temporary, destination)
            result.update(ok=True, source=f"релиз {release.get('tag', '')}")
            return result

        artifact = latest_build_artifact()
        if not artifact.get("url"):
            result["error"] = (
                "Готовой сборки нет: ни релиза, ни свежего результата сборки. "
                "Надёжнее всего выпустить релиз — создайте в репозитории тег "
                "вида v1.0, сборка соберётся сама и ляжет в Releases, откуда "
                "программа скачивает её без токена. Либо нажмите «Собрать "
                "новую версию» (нужен токен с правом Actions: Read and write).")
            return result

        say(f"Скачиваю сборку от {artifact.get('created', '')}...")
        archive_path = temporary + ".zip"
        try:
            download_binary(artifact["url"], archive_path,
                            accept="application/vnd.github+json", progress=progress)
        except urllib.error.HTTPError as e:
            if e.code not in (401, 403):
                raise
            # Проверено вживую на ОТКРЫТОМ репозитории: список сборок
            # отдаётся без токена (200), а сам файл сборки — только по
            # токену (403). Это правило GitHub, а не признак закрытого
            # репозитория. Прежний текст «Нет доступа к репозиторию. Для
            # закрытого нужен токен» отправлял человека заводить токен там,
            # где токен вообще не нужен: достаточно выпустить релиз.
            result["error"] = (
                "Файлы программы и советники обновились, а готовую сборку "
                ".exe скачать не удалось: она лежит в раз­деле Actions, а "
                "оттуда GitHub отдаёт файлы ТОЛЬКО по токену — даже для "
                "открытого репозитория. Токен здесь заводить не обязательно: "
                "проще выпустить релиз. Создайте в репозитории тег вида v1.0 "
                "(Releases -> Draft a new release -> Choose a tag -> v1.0 -> "
                "Publish) — сборка соберётся сама и ляжет в Releases, откуда "
                "программа скачивает её без всякого токена. Второй путь — "
                "токен с правом Actions: Read-only.")
            return result
        try:
            if not _extract_exe(archive_path, temporary):
                result["error"] = "В сборке не нашёлся .exe."
                return result
            broken = looks_like_program(temporary)
            if broken:
                result["error"] = ("Обновление не установлено: " + broken +
                                   ". Прежняя версия осталась на месте.")
                return result
        finally:
            try:
                os.remove(archive_path)
            except OSError:
                pass
        os.replace(temporary, destination)
        result.update(ok=True, source=f"сборка от {artifact.get('created', '')}")
        return result

    except Exception as e:  # noqa: BLE001
        result["error"] = explain_error(e)
        return result
    finally:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass


def request_build() -> str:
    """Просит GitHub собрать новую версию .exe. Пустая строка — получилось.

    Раньше это делалось руками: вкладка Actions -> Run workflow. Токену нужно
    право Actions: Read and write."""
    url = f"{API}/repos/{repo()}/actions/workflows/{BUILD_WORKFLOW}/dispatches"
    payload = json.dumps({"ref": branch()}).encode("utf-8")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "AI-Scalper-Updater",
        "Content-Type": "application/json",
    }
    if token():
        headers["Authorization"] = f"Bearer {token()}"
    else:
        return ("Для заказа сборки нужен токен GitHub с правом "
                "Actions: Read and write.")
    request = urllib.request.Request(url, data=payload, headers=headers,
                                     method="POST")
    try:
        urllib.request.urlopen(request, timeout=30)
        return ""
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return ("Токену не хватает прав. Нужно Actions: Read and write "
                    "(там же, где выдавали Contents).")
        if e.code == 404:
            return (f"Не найден сценарий сборки {BUILD_WORKFLOW} в ветке "
                    f"{branch()}. Проверьте репозиторий и ветку.")
        if e.code == 422:
            return (f"GitHub не принял запрос: сценарий {BUILD_WORKFLOW} должен "
                    f"разрешать ручной запуск (workflow_dispatch).")
        return explain_error(e)
    except Exception as e:  # noqa: BLE001
        return explain_error(e)


def build_status() -> dict:
    """Что сейчас со сборкой .exe: идёт, готова, упала.

    {"state", "text"} — state: "running" / "done" / "failed" / "none"."""
    url = (f"{API}/repos/{repo()}/actions/workflows/{BUILD_WORKFLOW}"
           f"/runs?per_page=1")
    try:
        with _request(url) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"state": "none", "text": explain_error(e)}

    runs = data.get("workflow_runs") or []
    if not runs:
        return {"state": "none", "text": "Сборок ещё не было."}

    run = runs[0]
    status = str(run.get("status", ""))
    conclusion = str(run.get("conclusion", ""))
    when = str(run.get("created_at", ""))[:16].replace("T", " ")
    if status != "completed":
        return {"state": "running",
                "text": f"Сборка идёт (запущена {when}). Обычно занимает 5-10 минут."}
    if conclusion == "success":
        return {"state": "done", "text": f"Сборка готова ({when})."}
    return {"state": "failed",
            "text": f"Сборка не удалась ({when}, {conclusion or 'без причины'}). "
                    f"Подробности — на GitHub, вкладка Actions."}


# =====================================================================
# ОДНА КНОПКА: ОБНОВИТЬ ВСЁ
# =====================================================================
def update_everything(progress=None) -> dict:
    """Советники + сама программа. Возвращает сводку для показа человеку."""
    def say(text):
        if progress:
            try:
                progress(text)
            except Exception:
                pass

    summary = {"errors": [], "lines": [], "restart_needed": False}

    say("Обновляю советники в MetaTrader...")
    advisors = update_advisors(progress=progress)
    if advisors.get("errors"):
        summary["errors"].extend(advisors["errors"])
    elif advisors.get("downloaded"):
        summary["lines"].append(
            f"Советники обновлены ({advisors['downloaded']} файлов). "
            + advisors.get("installed", ""))

    if is_frozen():
        say("Обновляю саму программу...")
        exe = download_new_exe(progress=progress)
        if exe.get("ok"):
            summary["lines"].append(
                f"Новая версия программы скачана ({exe['source']}). "
                f"Она встанет при следующем запуске.")
            summary["restart_needed"] = True
        elif exe.get("error"):
            summary["errors"].append(exe["error"])
    else:
        say("Обновляю файлы программы...")
        source = update_program_files(progress=progress)
        if source.get("errors"):
            summary["errors"].extend(source["errors"])
        elif source.get("replaced"):
            summary["lines"].append(
                f"Файлы программы обновлены ({source['replaced']} шт.). "
                f"Нужен перезапуск.")
            summary["restart_needed"] = True
        else:
            summary["lines"].append("Файлы программы уже свежие.")

    return summary


def pending_swap_path() -> str:
    """Куда кладётся скачанная новая версия программы."""
    if getattr(sys, "frozen", False):
        return sys.executable + ".new"
    return os.path.join(app_dir(), "update.new")


def apply_pending_swap(attempts: int = 12, pause: float = 0.5) -> str:
    """Вызывается ПРИ СТАРТЕ: если рядом лежит скачанная версия — меняем.

    Работающий .exe заменить нельзя, Windows его держит. Поэтому подмена
    делается в самом начале запуска, пока новый файл ещё не занят.

    Повторяем несколько раз с паузой: при автоматическом перезапуске старая
    копия программы может ещё не успеть закрыться, и первая попытка
    переименования упрётся в «файл занят». Это нормальная гонка на секунду, а
    не повод отказываться от обновления."""
    if not getattr(sys, "frozen", False):
        return ""
    new_path = pending_swap_path()
    if not os.path.exists(new_path):
        return ""

    # ПОДМЕНА ОДНОГО ФАЙЛА В УСТАНОВКЕ ПАПКОЙ ЗАПРЕЩЕНА. Рядом лежит папка
    # _internal с Python от ПРЕЖНЕЙ версии, и после подмены получилась бы
    # смесь двух разных сборок в одной папке. Файл, скачанный старой версией
    # программы (где этой заставы не было), удаляем — иначе он будет
    # напоминать о себе при каждом запуске.
    if installed_as_folder():
        try:
            os.remove(new_path)
        except OSError:
            pass
        log.warning("Установка папкой — подмена одного файла отменена")
        return "Обновление не применено. " + installer_advice()

    # ПОСЛЕДНЯЯ ЗАСТАВА ПЕРЕД ПОДМЕНОЙ. Файл мог быть скачан прежней версией
    # программы, где проверки не было, или испортиться на диске уже после
    # скачивания. Ставить обрубок на место работающей программы нельзя ни при
    # каких обстоятельствах: обратно её человек сам не вернёт.
    broken = looks_like_program(new_path)
    if broken:
        try:
            os.remove(new_path)
        except OSError:
            pass
        log.warning("Скачанное обновление негодно (%s) — не ставлю", broken)
        return ("Скачанное обновление оказалось повреждено (" + broken +
                "). Оно удалено, программа осталась прежней. "
                "Попробуйте обновиться ещё раз при устойчивой связи.")

    current = sys.executable
    backup = current + ".old"
    last_error = None
    for attempt in range(max(1, attempts)):
        try:
            if os.path.exists(backup):
                os.remove(backup)
            os.replace(current, backup)
            os.replace(new_path, current)
            return ("Программа обновлена. Перезапустите её, чтобы начать работу "
                    "в новой версии.")
        except OSError as e:
            last_error = e
            if attempt + 1 < attempts:
                time.sleep(pause)
    log.warning("Не удалось применить обновление: %s", last_error)
    return f"Не удалось применить обновление: {last_error}"


def restart_program() -> str:
    """Запускает программу заново и завершает текущую копию.

    Зачем это нужно. Обновление меняет файлы на диске, но в уже работающем
    процессе загружены СТАРЫЕ модули. Работать дальше — значит смешивать две
    версии: часть кода старая, часть новая, и при первом же расхождении в
    именах функций программа упадёт в самом неожиданном месте. Честный
    перезапуск дешевле любой попытки «подгрузить на ходу».

    Возвращает текст ошибки; при успехе не возвращается вовсе — процесс
    завершается."""
    try:
        if getattr(sys, "frozen", False):
            command = [sys.executable]
        else:
            command = [sys.executable, os.path.abspath(sys.argv[0])]
        command.extend(sys.argv[1:])
        subprocess.Popen(command, close_fds=True)
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось перезапустить программу: %s", e)
        return f"Не удалось перезапустить программу: {e}"

    # os._exit, а не sys.exit: sys.exit бросает исключение, которое перехватит
    # обработчик выше, и программа продолжит работать двумя копиями сразу.
    log.info("Перезапуск после обновления.")
    os._exit(0)


def remember_revision(revision: str, write_config) -> None:
    """Запоминает установленную версию. write_config — функция записи в
    config.py, передаётся снаружи, чтобы этот модуль не зависел от интерфейса."""
    try:
        write_config("UPDATE_INSTALLED_REV", repr(revision))
    except Exception as e:
        log.warning("Не удалось записать номер версии: %s", e)


def recent_changes(limit: int = 20):
    """Последние изменения в репозитории — просто посмотреть, что сделано.

    Возвращает (список, ошибка). Каждая запись: {"revision", "date", "message"}.
    Ничего не скачивает и не устанавливает: это справка, а не обновление."""
    if not enabled():
        return [], "Синхронизация выключена в настройках."
    if not repo() or "/" not in repo():
        return [], ("Не задан репозиторий. Впишите его в виде владелец/название "
                    "на вкладке «Система».")

    url = f"{API}/repos/{repo()}/commits?sha={branch()}&per_page={int(limit)}"
    try:
        with _request(url) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        return [], explain_error(e)

    if not isinstance(data, list):
        return [], "GitHub вернул неожиданный ответ."

    entries = []
    for item in data:
        commit = item.get("commit") or {}
        author = commit.get("author") or {}
        raw_date = str(author.get("date", ""))
        # 2026-08-03T12:34:56Z -> 03.08 12:34
        when = raw_date
        if len(raw_date) >= 16:
            when = f"{raw_date[8:10]}.{raw_date[5:7]} {raw_date[11:16]}"
        message = str(commit.get("message", "")).splitlines()
        entries.append({
            "revision": str(item.get("sha", ""))[:12],
            "date": when,
            "message": message[0][:150] if message else "",
        })
    return entries, ""
