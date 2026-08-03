"""
mt5_install.py — установка советников и сервиса в MetaTrader 5 прямо из
программы. Ничего ставить отдельно не нужно.

ЧТО ДЕЛАЕТ
  1. Находит все установленные терминалы MetaTrader 5 — и обычные, и
     портативные.
  2. Копирует в каждый:
       * AI_Scalper_Pro.mq5 + .mqh  -> MQL5/Experts
       * DualGuardEA.mq5            -> MQL5/Experts
       * CalendarExport.mq5         -> MQL5/Services  (бесплатный календарь)
  3. Компилирует их: MetaEditor умеет собирать из командной строки
     (metaeditor64.exe /compile:...), поэтому жать F7 руками не нужно.

ОТКУДА БЕРУТСЯ ФАЙЛЫ
Исходники .mq5/.mqh кладутся внутрь программы при сборке .exe (см.
build-exe.yml, ключ --add-data). При запуске из исходников берутся прямо из
папок репозитория. bundled_root() разбирается с обоими случаями.

ЧЕГО ЗДЕСЬ НЕТ
Никакой торговли и никаких настроек: модуль только копирует файлы и зовёт
компилятор. Если что-то не вышло — возвращает понятный текст, а не молчит.
"""

import os
import shutil
import subprocess
import sys

# Что и куда копируем. Ключ — папка внутри MQL5 у терминала.
LAYOUT = {
    "Experts": [
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
        ("mql5", "DualGuardEA.mq5"),
    ],
    "Services": [
        ("mql5", "CalendarExport.mq5"),
    ],
}

# Что компилировать после копирования (папка внутри MQL5, имя файла).
# .mqh — это заголовки, они собираются вместе с .mq5, отдельно не компилируются.
COMPILE = [
    ("Experts", "AI_Scalper_Pro.mq5"),
    ("Experts", "DualGuardEA.mq5"),
    ("Services", "CalendarExport.mq5"),
]

SERVICE_DIRS = ("Experts", "Services", "Files")


def bundled_root() -> str:
    """Папка, откуда брать исходники .mq5.

    В собранной программе PyInstaller распаковывает вложенные файлы во
    временную папку и кладёт путь в sys._MEIPASS. При запуске из исходников
    поднимаемся на уровень выше — в корень репозитория."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def source_path(subdir: str, name: str) -> str:
    return os.path.join(bundled_root(), subdir, name)


def sources_available() -> bool:
    """Есть ли рядом исходники советников. Если программу запустили без них,
    честнее сказать об этом, чем делать вид, что установка прошла."""
    return os.path.exists(source_path("mql5", "CalendarExport.mq5"))


# =====================================================================
# ПОИСК ТЕРМИНАЛОВ
# =====================================================================
def _candidate_roots() -> list:
    """Где вообще искать терминалы. Вынесено отдельно, чтобы тесты могли
    подставить свои папки вместо системных."""
    roots = []
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        roots.append(os.path.join(appdata, "MetaQuotes", "Terminal"))
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(var, "")
        if base:
            roots.append(base)
    return roots


def find_terminals(roots=None) -> list:
    """Список папок данных терминалов (там, где лежит подпапка MQL5).

    Служебные папки Common и Community терминалами не являются — их
    пропускаем, иначе установка «прошла бы» в никуда."""
    found = []
    for root in (roots if roots is not None else _candidate_roots()):
        if not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for entry in entries:
            if entry in ("Common", "Community"):
                continue
            path = os.path.join(root, entry)
            if os.path.isdir(os.path.join(path, "MQL5")) and path not in found:
                found.append(path)
    return found


def find_metaeditor(terminal_dir: str) -> str:
    """Путь к MetaEditor для этого терминала или "".

    У портативной установки редактор лежит рядом с terminal64.exe. У обычной
    папка данных (AppData) и папка программы разные, поэтому проверяем ещё и
    стандартные места установки."""
    names = ("metaeditor64.exe", "metaeditor.exe")

    for name in names:
        candidate = os.path.join(terminal_dir, name)
        if os.path.isfile(candidate):
            return candidate

    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(var, "")
        if not base or not os.path.isdir(base):
            continue
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            continue
        for entry in entries:
            for name in names:
                candidate = os.path.join(base, entry, name)
                if os.path.isfile(candidate):
                    return candidate
    return ""


# =====================================================================
# КОПИРОВАНИЕ И КОМПИЛЯЦИЯ
# =====================================================================
def copy_files(terminal_dir: str) -> tuple:
    """Копирует все файлы в один терминал. Возвращает (сколько, ошибки)."""
    copied = 0
    errors = []
    mql5 = os.path.join(terminal_dir, "MQL5")

    for folder in SERVICE_DIRS:
        try:
            os.makedirs(os.path.join(mql5, folder), exist_ok=True)
        except OSError as e:
            errors.append(f"не удалось создать {folder}: {e}")

    for folder, items in LAYOUT.items():
        target_dir = os.path.join(mql5, folder)
        for subdir, name in items:
            src = source_path(subdir, name)
            if not os.path.exists(src):
                errors.append(f"нет исходника {name}")
                continue
            try:
                shutil.copy2(src, os.path.join(target_dir, name))
                copied += 1
            except OSError as e:
                # Самая частая причина — открытый MetaEditor держит файл
                errors.append(f"{name}: {e}")
    return copied, errors


def compile_one(metaeditor: str, mq5_path: str, timeout: int = 120) -> str:
    """Компилирует один файл. Возвращает "" при успехе или текст ошибки.

    MetaEditor поддерживает сборку из командной строки — именно это избавляет
    от ручного F7. Код возврата у него означает число ошибок компиляции,
    поэтому 0 и 1 (только предупреждения) считаем успехом."""
    log_path = mq5_path + ".log"
    try:
        result = subprocess.run(
            [metaeditor, f"/compile:{mq5_path}", f"/log:{log_path}"],
            capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"{os.path.basename(mq5_path)}: компиляция не уложилась в {timeout} с"
    except OSError as e:
        return f"{os.path.basename(mq5_path)}: не удалось запустить MetaEditor ({e})"

    if result.returncode in (0, 1):
        return ""

    detail = ""
    try:
        if os.path.exists(log_path):
            # MetaEditor пишет журнал в UTF-16
            with open(log_path, "r", encoding="utf-16", errors="replace") as f:
                lines = [ln.strip() for ln in f if "error" in ln.lower()]
            detail = "; ".join(lines[:3])
    except OSError:
        pass
    return f"{os.path.basename(mq5_path)}: ошибок компиляции {result.returncode}. {detail}".strip()


def install_all(progress=None, compile_files: bool = True) -> dict:
    """Ставит всё во все найденные терминалы.

    Возвращает отчёт: {"terminals", "copied", "compiled", "errors", "text"}.
    progress(текст) — необязательный обработчик для показа хода работы."""
    def say(text):
        if progress:
            try:
                progress(text)
            except Exception:
                pass

    report = {"terminals": 0, "copied": 0, "compiled": 0, "errors": [], "text": ""}

    if not sources_available():
        report["errors"].append(
            "Рядом с программой нет исходников советников (.mq5). "
            "Скачайте полную сборку или запустите программу из папки проекта.")
        report["text"] = report["errors"][0]
        return report

    terminals = find_terminals()
    if not terminals:
        report["errors"].append(
            "Не найден ни один терминал MetaTrader 5. Установите и запустите "
            "терминал хотя бы один раз, затем повторите.")
        report["text"] = report["errors"][0]
        return report

    for terminal in terminals:
        say(f"Копирую в {os.path.basename(terminal)}...")
        copied, errors = copy_files(terminal)
        report["terminals"] += 1
        report["copied"] += copied
        report["errors"].extend(errors)

        if not compile_files:
            continue

        metaeditor = find_metaeditor(terminal)
        if not metaeditor:
            report["errors"].append(
                "MetaEditor не найден — файлы скопированы, но не собраны. "
                "Откройте MetaEditor (F4 в терминале) и нажмите F7.")
            continue

        for folder, name in COMPILE:
            path = os.path.join(terminal, "MQL5", folder, name)
            if not os.path.exists(path):
                continue
            say(f"Собираю {name}...")
            problem = compile_one(metaeditor, path)
            if problem:
                report["errors"].append(problem)
            else:
                report["compiled"] += 1

    report["text"] = describe(report)
    return report


def describe(report: dict) -> str:
    """Отчёт человеческим языком."""
    if report["terminals"] == 0:
        return report["errors"][0] if report["errors"] else "Терминалы не найдены."

    parts = [f"Терминалов: {report['terminals']}, файлов скопировано: {report['copied']}"]
    if report["compiled"]:
        parts.append(f"собрано советников: {report['compiled']}")
    if report["errors"]:
        parts.append("Замечания: " + "; ".join(report["errors"][:3]))
        if len(report["errors"]) > 3:
            parts.append(f"и ещё {len(report['errors']) - 3}")
    else:
        parts.append("ошибок нет")
    return ". ".join(parts) + "."


def is_installed() -> bool:
    """Похоже ли, что установка уже была: хотя бы в одном терминале лежит
    сервис календаря."""
    for terminal in find_terminals():
        if os.path.exists(os.path.join(terminal, "MQL5", "Services", "CalendarExport.mq5")):
            return True
    return False
