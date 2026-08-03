"""
updater.py — обновление программы из GitHub без переустановки.

ЗАЧЕМ
Вы правите код, он уезжает на GitHub, а на рабочем компьютере программа
остаётся старой. Переустанавливать её каждый раз незачем — обновление
скачивается и применяется само.

ЧТО ОБНОВЛЯЕТСЯ БЕЗ ПЕРЕЗАПУСКА
Советники и сервис календаря (.mq5/.mqh). Это обычные текстовые файлы: их
достаточно положить в терминал и пересобрать, что программа уже умеет
(mt5_install). Перезапуск не нужен.

ЧТО ТРЕБУЕТ ПЕРЕЗАПУСКА
Сама программа (.exe). Работающий файл заменить нельзя — Windows его держит.
Поэтому новая версия скачивается рядом, а подмена происходит при следующем
запуске: программа видит файл .new, меняет его местами со старым и стартует.

ПРИВАТНЫЙ РЕПОЗИТОРИЙ
Если репозиторий закрытый, нужен токен GitHub с правом чтения содержимого
(Settings -> Developer settings -> Personal access tokens -> Fine-grained,
доступ Contents: Read-only). Токен — такой же секрет, как ключи API: он
хранится в config.py и шифруется наравне с ними.

ЧЕГО ЗДЕСЬ НЕТ
Обновление никогда не ставится молча в фоне. Программа проверяет наличие
новой версии и СПРАШИВАЕТ. Подменять торгового робота без ведома человека,
пока у него открыты позиции, недопустимо.
"""

import json
import logging
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request

import config as cfg

log = logging.getLogger("updater")

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"

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


def branch() -> str:
    return str(getattr(cfg, "UPDATE_BRANCH", "main") or "main").strip()


def token() -> str:
    return str(getattr(cfg, "UPDATE_TOKEN", "") or "").strip()


def current_version() -> str:
    """Что установлено сейчас. Пишется при удачном обновлении."""
    return str(getattr(cfg, "UPDATE_INSTALLED_REV", "") or "")


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _request(url: str, accept: str = "application/vnd.github+json", timeout: int = 20):
    """Запрос к GitHub с токеном, если он задан."""
    headers = {"Accept": accept, "User-Agent": "AI-Scalper-Updater"}
    if token():
        headers["Authorization"] = f"Bearer {token()}"
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout)


def explain_error(exc: Exception) -> str:
    """Ошибку сети — понятной фразой."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return ("Репозиторий или ветка не найдены. Проверьте UPDATE_REPO и "
                    "UPDATE_BRANCH. Для закрытого репозитория нужен токен.")
        if exc.code in (401, 403):
            return ("Нет доступа к репозиторию. Для закрытого нужен токен GitHub "
                    "с правом Contents: Read-only.")
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

    url = f"{API}/repos/{repo()}/commits/{branch()}"
    try:
        with _request(url) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as e:
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
    """Один файл из репозитория как текст."""
    url = f"{RAW}/{repo()}/{branch()}/{path}"
    with _request(url, accept="text/plain") as response:
        return response.read().decode("utf-8", errors="replace")


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


def pending_swap_path() -> str:
    """Куда кладётся скачанная новая версия программы."""
    if getattr(sys, "frozen", False):
        return sys.executable + ".new"
    return os.path.join(app_dir(), "update.new")


def apply_pending_swap() -> str:
    """Вызывается ПРИ СТАРТЕ: если рядом лежит скачанная версия — меняем.

    Работающий .exe заменить нельзя, Windows его держит. Поэтому подмена
    делается в самом начале запуска, пока новый файл ещё не занят."""
    if not getattr(sys, "frozen", False):
        return ""
    new_path = pending_swap_path()
    if not os.path.exists(new_path):
        return ""

    current = sys.executable
    backup = current + ".old"
    try:
        if os.path.exists(backup):
            os.remove(backup)
        os.replace(current, backup)
        os.replace(new_path, current)
        return "Программа обновлена. Перезапустите её, чтобы начать работу в новой версии."
    except OSError as e:
        log.warning("Не удалось применить обновление: %s", e)
        return f"Не удалось применить обновление: {e}"


def remember_revision(revision: str, write_config) -> None:
    """Запоминает установленную версию. write_config — функция записи в
    config.py, передаётся снаружи, чтобы этот модуль не зависел от интерфейса."""
    try:
        write_config("UPDATE_INSTALLED_REV", repr(revision))
    except Exception as e:
        log.warning("Не удалось записать номер версии: %s", e)
