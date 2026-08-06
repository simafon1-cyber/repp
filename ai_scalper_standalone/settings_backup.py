"""settings_backup.py — настройки не теряются при обновлении и переносе.

ЗАЧЕМ
Владелец: «сбиваются последние настройки, пусть сохраняет последние
установленные настройки».

Причина в том, ГДЕ живёт config.py. Программа ищет его РЯДОМ С СОБОЙ
(os.path.dirname(sys.executable)). Пока .exe лежит на одном месте, всё
хорошо. Но стоит запустить свежескачанную сборку из другой папки — из
«Загрузок», с флешки, из распакованного архива — рядом с ней никакого
config.py нет. Программа честно создаёт новый из эталона, и человек видит
заводские настройки вместо своих: ключи, логин брокера, изменённые пороги —
всё «сбилось».

ЧТО ДЕЛАЕТ ЭТОТ МОДУЛЬ
Держит копию настроек в ПОСТОЯННОЙ папке пользователя (%APPDATA%\\AI_Scalper
на Windows, ~/.config/ai_scalper на остальных). Она не зависит от того,
откуда запущен .exe, и переживает и обновление, и перенос программы.

  * при каждом запуске рабочий config.py копируется туда;
  * если рабочего config.py нет, а копия есть — настройки возвращаются из
    неё, а не берутся заводские.

ЧЕГО ЗДЕСЬ НЕТ
Копия НЕ перезаписывает существующий config.py. Восстановление происходит
только когда рабочего файла нет вовсе. Иначе одна старая копия могла бы
затереть то, что человек только что настроил, — а это ровно та беда, от
которой модуль и защищает.
"""

import logging
import os
import shutil
import sys

log = logging.getLogger("settings_backup")

FOLDER_NAME = "AI_Scalper"
BACKUP_NAME = "config.py"


def app_dir() -> str:
    """Папка рядом с программой — там лежит рабочий config.py."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def storage_dir() -> str:
    """Постоянная папка пользователя. Не зависит от того, откуда запущен .exe."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, FOLDER_NAME)
    home = os.path.expanduser("~")
    return os.path.join(home, ".config", "ai_scalper")


def backup_path() -> str:
    return os.path.join(storage_dir(), BACKUP_NAME)


def config_path() -> str:
    return os.path.join(app_dir(), "config.py")


def save(source: str = "") -> str:
    """Сохранить текущие настройки в постоянную папку.

    Возвращает путь к копии или "" — сохранять было нечего либо не вышло."""
    source = source or config_path()
    if not os.path.exists(source):
        return ""
    target = backup_path()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        # Через временный файл: обрыв на середине не должен оставить
        # обрезанную копию вместо целой — именно она потом восстанавливается.
        temporary = target + ".tmp"
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    except OSError as e:
        log.warning("Не удалось сохранить копию настроек в %s: %s", target, e)
        return ""
    return target


def restore_if_missing(target: str = "") -> str:
    """Вернуть настройки из постоянной папки, если рабочего файла нет.

    Возвращает путь к восстановленному файлу или "" (восстанавливать нечего
    или незачем). СУЩЕСТВУЮЩИЙ config.py не трогается никогда."""
    target = target or config_path()
    if os.path.exists(target):
        return ""          # свои настройки на месте — не лезем
    source = backup_path()
    if not os.path.exists(source):
        return ""          # копии нет — обычный первый запуск
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(source, target)
    except OSError as e:
        log.warning("Не удалось восстановить настройки из %s: %s", source, e)
        return ""
    log.info("Настройки восстановлены из %s — рядом с программой их не было "
             "(запуск из другой папки или после переустановки).", source)
    return target


def describe() -> str:
    """Где лежит копия — для вкладки «Система»."""
    path = backup_path()
    if not os.path.exists(path):
        return f"Копия настроек ещё не создана. Будет здесь: {path}"
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return f"Копия настроек: {path} ({size} байт)"
