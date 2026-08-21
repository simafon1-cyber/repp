"""
diagnostics.py — проверка, что на этом компьютере всё есть для работы.

Смысл: человек ставит программу на новую систему и должен сразу видеть, чего
не хватает, а не гадать, почему «не работает». Каждая проверка отвечает на
три вопроса: что проверяли, каков результат, что делать если плохо.

Ничего не чинит сам и ничего не устанавливает — только смотрит и сообщает.
Молчаливая самодеятельность на чужом компьютере хуже честного отчёта.
"""

import importlib.util
import os
import shutil
import sys

OK = "ok"            # всё хорошо
WARN = "warn"        # работать будет, но хуже
FAIL = "fail"        # без этого не заработает


def _check(name: str, level: str, detail: str, fix: str = "") -> dict:
    return {"name": name, "level": level, "detail": detail, "fix": fix}


def check_python() -> dict:
    """Версия Python. В собранной программе он свой, встроенный — тогда
    проверять на компьютере нечего."""
    if getattr(sys, "frozen", False):
        return _check("Python", OK,
                      f"встроен в программу (версия {sys.version_info.major}."
                      f"{sys.version_info.minor}) — ставить отдельно не нужно")
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info < (3, 10):
        return _check("Python", FAIL, f"версия {version} слишком старая",
                      "Нужен Python 3.10 или новее — python.org")
    return _check("Python", OK, f"версия {version}")


# Что нужно программе. Обязательные и необязательные разделены: без первых
# она не работает, без вторых теряет отдельную возможность.
REQUIRED_PACKAGES = {
    "MetaTrader5": "связь с терминалом",
    "pandas": "расчёт индикаторов",
    "numpy": "расчёт индикаторов",
}
OPTIONAL_PACKAGES = {
    "telethon": "сигналы из Telegram",
    "anthropic": "ИИ-сигнал от Claude",
    "requests": "новости из внешнего API",
    "cryptography": "шифрование секретов",
    "openpyxl": "экспорт в Excel",
    "pystray": "значок в трее",
}


def check_packages() -> list:
    """Наличие библиотек. В собранной программе они уже внутри."""
    frozen = getattr(sys, "frozen", False)
    results = []

    for name, why in REQUIRED_PACKAGES.items():
        present = importlib.util.find_spec(name) is not None
        if present:
            results.append(_check(name, OK, why))
        elif frozen:
            results.append(_check(name, FAIL, f"нет в сборке ({why})",
                                  "Пересоберите программу — это ошибка сборки"))
        else:
            results.append(_check(name, FAIL, f"не установлен ({why})",
                                  f"pip install {name}"))

    for name, why in OPTIONAL_PACKAGES.items():
        present = importlib.util.find_spec(name) is not None
        if present:
            results.append(_check(name, OK, why))
        else:
            results.append(_check(name, WARN, f"нет — не будет работать: {why}",
                                  f"pip install {name}"))
    return results


def check_terminals() -> dict:
    try:
        import mt5_install
        terminals = mt5_install.find_terminals()
    except Exception as e:
        return _check("MetaTrader 5", WARN, f"не удалось проверить ({e})", "")

    if not terminals:
        return _check("MetaTrader 5", FAIL, "терминал не найден",
                      "Установите MetaTrader 5 и запустите его хотя бы один раз")
    return _check("MetaTrader 5", OK, f"найдено терминалов: {len(terminals)}")


def check_metaeditor() -> dict:
    try:
        import mt5_install
        terminals = mt5_install.find_terminals()
        if not terminals:
            return _check("MetaEditor", WARN, "проверять нечего — нет терминалов", "")
        for terminal in terminals:
            if mt5_install.find_metaeditor(terminal):
                return _check("MetaEditor", OK, "найден — советники собираются сами")
    except Exception as e:
        return _check("MetaEditor", WARN, f"не удалось проверить ({e})", "")
    return _check("MetaEditor", WARN, "не найден — советники придётся собрать вручную",
                  "Откройте MetaEditor (F4 в терминале) и нажмите F7")


def check_advisors_installed() -> dict:
    try:
        import mt5_install
        if mt5_install.is_installed():
            return _check("Советники в MT5", OK, "установлены")
        return _check("Советники в MT5", WARN, "ещё не установлены",
                      "Кнопка «Установить в MetaTrader» на вкладке «Источники»")
    except Exception as e:
        return _check("Советники в MT5", WARN, f"не удалось проверить ({e})", "")


def check_config() -> dict:
    """Файл настроек рядом с программой."""
    app_dir = os.path.dirname(os.path.abspath(__file__))
    if getattr(sys, "frozen", False):
        app_dir = os.path.dirname(sys.executable)
    path = os.path.join(app_dir, "config.py")
    if os.path.exists(path):
        return _check("Настройки (config.py)", OK, path)
    return _check("Настройки (config.py)", WARN, "файла нет — будут значения по умолчанию",
                  "Программа создаст его при первом сохранении настроек")


def check_disk() -> dict:
    app_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        free_mb = shutil.disk_usage(app_dir).free / (1024 * 1024)
    except OSError as e:
        return _check("Свободное место", WARN, f"не удалось проверить ({e})", "")
    if free_mb < 200:
        return _check("Свободное место", FAIL, f"{free_mb:.0f} МБ — мало",
                      "Освободите хотя бы 500 МБ: журналы и обновления не поместятся")
    return _check("Свободное место", OK, f"{free_mb / 1024:.1f} ГБ")


def check_write_access() -> dict:
    """Право записи рядом с программой. Без него не сохранятся ни настройки,
    ни журнал сделок, ни статистика обучения."""
    app_dir = os.path.dirname(os.path.abspath(__file__))
    if getattr(sys, "frozen", False):
        app_dir = os.path.dirname(sys.executable)
    probe = os.path.join(app_dir, ".write_test")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return _check("Запись в папку программы", OK, app_dir)
    except OSError as e:
        return _check("Запись в папку программы", FAIL, f"нет доступа ({e})",
                      "Переустановите программу в папку пользователя, "
                      "например в Загрузки или в Документы")


def check_bridge() -> dict:
    try:
        import bridge_host
        if not bridge_host.enabled():
            return _check("Мост для советников", WARN, "выключен в настройках",
                          "Включите на вкладке «Источники», если пользуетесь советниками MT5")
        if bridge_host.is_running():
            return _check("Мост для советников", OK, bridge_host.status()["detail"])
        return _check("Мост для советников", WARN, bridge_host.status()["detail"], "")
    except Exception as e:
        return _check("Мост для советников", WARN, f"не удалось проверить ({e})", "")


def run_all() -> list:
    """Все проверки подряд. Порядок — от самого важного к мелочам."""
    results = [
        check_python(),
        check_terminals(),
        check_metaeditor(),
        check_advisors_installed(),
        check_write_access(),
        check_config(),
        check_disk(),
        check_bridge(),
    ]
    results.extend(check_packages())
    return results


def summary(results: list) -> str:
    fails = [r for r in results if r["level"] == FAIL]
    warns = [r for r in results if r["level"] == WARN]
    if fails:
        return f"Не хватает необходимого: {len(fails)}. Программа работать не будет."
    if warns:
        return f"Всё основное на месте. Замечаний: {len(warns)} — часть возможностей выключена."
    return "Всё на месте, программа готова к работе."


def has_blocking_problems(results: list) -> bool:
    return any(r["level"] == FAIL for r in results)
