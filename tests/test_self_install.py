#!/usr/bin/env python3
"""Тесты самообновления установки ПАПКОЙ.

ПОЧЕМУ ЭТИ ТЕСТЫ ПОЯВИЛИСЬ.

Программа ставится установщиком, то есть папкой. А самообновление умело
только подменить один .exe — для папки это неверно и ломает установку.
Поэтому оно честно отказывалось работать и советовало «скачайте установщик
руками». Совет верный, но это НЕ самообновление: владелец нажимал кнопку и
получал отписку.

Теперь программа скачивает установщик сама и ставит его тихо при закрытии.
Место опасное: сценарий-посредник выполняется УЖЕ ПОСЛЕ того, как программа
закрылась, и если он написан неверно, чинить будет некому и нечем — окна
больше нет. Поэтому здесь проверяется каждая его строка.

Запуск:  python3 tests/test_self_install.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
APP = ROOT / "ai_scalper_standalone"
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
        print(f"  СБОЙ {name}" + (f" -> {detail}" if detail else ""))


cfg = types.ModuleType("config")
cfg.__file__ = str(APP / "config.py.example")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg
CFG = cfg

import updater  # noqa: E402


def подделка(путь, размер=None, заголовок=b"MZ", метка=True):
    """Файл, похожий (или нет) на установщик."""
    размер = размер if размер is not None else updater.MIN_EXE_BYTES + 4096
    тело = bytearray(b"\0" * размер)
    тело[0:len(заголовок)] = заголовок
    if метка:
        тело[100000:100000 + len(updater.INNO_MARKER)] = updater.INNO_MARKER
    with open(путь, "wb") as f:
        f.write(тело)
    return путь


# =====================================================================
def test_installer_is_recognised_correctly() -> None:
    """У установщика НЕТ метки PyInstaller — она есть только у самой
    программы. Взять для него чужую проверку значило бы на каждом обновлении
    объявлять целый файл оборванным."""
    print("\n[Установщик опознаётся своей меткой, а не чужой]")
    with tempfile.TemporaryDirectory() as папка:
        годный = подделка(os.path.join(папка, "ok.exe"))
        check(updater.looks_like_installer(годный) == "",
              "Настоящий установщик признан годным")

        # А проверка для программы его бы забраковала — это и есть та ловушка,
        # ради которой проверки разделены.
        check(updater.looks_like_program(годный) != "",
              "Проверкой для программы он бы НЕ прошёл — потому и своя")

        обрывок = подделка(os.path.join(папка, "small.exe"), размер=1024)
        check("обрывок" in updater.looks_like_installer(обрывок),
              "Обрывок отвергнут", updater.looks_like_installer(обрывок))

        страница = подделка(os.path.join(папка, "page.exe"), заголовок=b"<!")
        check("не приложение Windows" in updater.looks_like_installer(страница),
              "Страница с ошибкой вместо файла отвергнута")

        чужой = подделка(os.path.join(папка, "alien.exe"), метка=False)
        check("не установщик" in updater.looks_like_installer(чужой),
              "Чужая программа без метки установщика отвергнута",
              updater.looks_like_installer(чужой))

        check("не читается" in updater.looks_like_installer(
            os.path.join(папка, "нет-такого.exe")),
            "Отсутствующий файл — тоже негоден")


def test_install_script_does_the_right_things_in_the_right_order() -> None:
    """ГЛАВНЫЙ ТЕСТ. Сценарий выполняется, когда программа уже закрыта:
    ошибку в нём исправлять будет нечем и некому."""
    print("\n[Сценарий установки делает всё в правильном порядке]")
    текст = updater._installer_script(
        r"C:\Users\Иван\AppData\Local\AI Scalper Pro\AI_Scalper_Setup.exe.new",
        r"C:\Users\Иван\AppData\Local\AI Scalper Pro",
        r"C:\Users\Иван\AppData\Local\AI Scalper Pro\AI_Scalper_Pro.exe",
        4242)

    check("tasklist" in текст and "4242" in текст,
          "Ждёт, пока закроется именно наша копия — по её номеру процесса")
    check(текст.index("tasklist") < текст.index("/VERYSILENT"),
          "И ждёт ДО установки, а не после: иначе файлы заняты")
    check("/VERYSILENT" in текст, "Ставит без окон")
    check("/SUPPRESSMSGBOXES" in текст, "И без вопросов, нажать которые некому")
    check("/NORESTART" in текст, "И не перезагружает компьютер")
    check('/DIR="C:\\Users\\Иван\\AppData\\Local\\AI Scalper Pro"' in текст,
          "Ставит в ТУ ЖЕ папку, а не рядом")
    check("/LOG=" in текст, "И оставляет журнал установки — иначе разбирать нечего")
    check(текст.index("/VERYSILENT") < текст.index("start "),
          "Запускает программу ПОСЛЕ установки")
    check("AI_Scalper_Pro.exe" in текст, "Запускает именно программу")
    check(текст.count("AI_Scalper_Setup.exe.new") >= 2
          and "del " in текст, "Убирает за собой скачанный установщик")
    check('del "%~f0"' in текст, "И удаляет сам себя — иначе останется навсегда")
    check(текст.index("start ") < текст.index('del "%~f0"'),
          "Но удаляет себя последним, а не до запуска программы")

    # Ничего лишнего он удалять не должен.
    for опасное in ("config.py", "accounts.json", "trades_log", "/S ", "rmdir",
                    "rd /", "format", "*.py"):
        check(опасное not in текст,
              f"Сценарий не трогает {опасное.strip()}")

    check(текст.count("\r\n") >= 5,
          "Переводы строк как в Windows — иначе командный процессор не прочтёт")


def test_install_refuses_when_file_is_missing_or_broken() -> None:
    """Запустить установку негодным файлом — значит сломать рабочую программу
    руками. Проверка стоит ДО закрытия окна."""
    print("\n[Негодный установщик до установки не допускается]")
    было = updater.app_dir
    try:
        with tempfile.TemporaryDirectory() as папка:
            updater.app_dir = lambda: папка

            ответ = updater.install_downloaded(os.path.join(папка, "нет.exe"))
            check("сначала нажмите" in ответ.lower() or "не скачан" in ответ,
                  "Без скачанного файла — понятный отказ", ответ)

            плохой = подделка(os.path.join(папка, "bad.exe"), метка=False)
            ответ = updater.install_downloaded(плохой)
            check("негоден" in ответ, "Негодный файл — отказ", ответ[:60])
            check(not os.path.exists(плохой),
                  "И он удалён, чтобы не мешать следующей попытке")
    finally:
        updater.app_dir = было


def test_folder_install_downloads_installer_instead_of_refusing() -> None:
    """ПОЧЕМУ ЭТО И ЕСТЬ ПОЧИНКА. Раньше здесь стоял отказ: программа
    советовала скачать установщик руками. Кнопка «Обновить всё сейчас»
    не обновляла ничего."""
    print("\n[Установка папкой обновляется сама, а не отпиской]")
    было_папкой = updater.installed_as_folder
    было_скачать = updater.download_installer
    try:
        updater.installed_as_folder = lambda: True
        updater.download_installer = lambda progress=None: {
            "ok": True, "path": r"C:\prog\AI_Scalper_Setup.exe.new",
            "tag": "build-63", "error": ""}

        итог = updater.download_new_exe()
        check(итог["ok"], "Обновление состоялось, а не отказано")
        check(итог["installer"].endswith(".new"),
              "И вернулся путь к скачанному установщику", итог["installer"])
        check("build-63" in итог["source"], "С номером версии", итог["source"])

        # А если скачать не вышло — честная ошибка, а не молчаливое «ок».
        updater.download_installer = lambda progress=None: {
            "ok": False, "path": "", "tag": "", "error": "нет связи"}
        итог = updater.download_new_exe()
        check(not итог["ok"] and итог["error"] == "нет связи",
              "Неудача скачивания не выдаётся за успех")
    finally:
        updater.installed_as_folder = было_папкой
        updater.download_installer = было_скачать


def test_settings_are_never_touched_by_the_installer() -> None:
    """Обновление, затирающее настройки и ключи, — это не обновление, а
    потеря данных. У установщика за это отвечает одна строка, и она должна
    быть на месте."""
    print("\n[Установщик не трогает настройки и ключи]")
    iss = (APP / "installer.iss").read_text(encoding="utf-8", errors="replace")

    check("onlyifdoesntexist" in iss,
          "config.py ставится только если его ещё нет")
    строка_конфига = [l for l in iss.splitlines()
                      if "config.py" in l and "DestDir" in l]
    check(any("onlyifdoesntexist" in l for l in строка_конфига),
          "И это именно про config.py, а не про что-то другое",
          str(строка_конфига)[:120])
    check('Excludes: "config.py"' in iss,
          "А из папки программы он исключён — иначе перезаписался бы")
    check("PrivilegesRequired=lowest" in iss,
          "Установка без прав администратора — иначе тихо поставить нельзя")
    check("{localappdata}" in iss,
          "И в папку пользователя, где эти права есть")
    check("AppId=" in iss,
          "Постоянный номер программы — новая версия ложится поверх старой")

    # Ни один защищённый файл не должен упоминаться ДЕЙСТВУЮЩЕЙ строкой.
    # Закомментированные строки не считаются: строка, начинающаяся с точки с
    # запятой, для Inno Setup не существует. Требовать, чтобы имени не было
    # даже в пояснении, — придирка, а не проверка.
    действующие = [l.strip() for l in iss.splitlines()
                   if l.strip() and not l.strip().startswith(";")]
    for имя in ("accounts.json", "telegram_session", "trades_log.csv",
                "learning_state.json", "scalper.log"):
        задет = [l for l in действующие if имя in l]
        check(not задет,
              f"Ни одна действующая строка установщика не трогает {имя}",
              str(задет)[:100])

    # И удаление программы тоже не должно уносить историю сделок.
    удаление = iss[iss.index("[UninstallDelete]"):] if "[UninstallDelete]" in iss else ""
    живые = [l.strip() for l in удаление.splitlines()
             if l.strip().startswith("Type:")]
    check(not живые,
          "При удалении программы журналы и история сделок остаются на диске",
          str(живые)[:100])


def test_protected_files_still_protected() -> None:
    """Список защищённого не должен усохнуть по дороге."""
    print("\n[Список защищённых файлов на месте]")
    for имя in ("config.py", "accounts.json", "telegram_session",
                "trades_log.csv"):
        check(any(имя in str(p) for p in updater.PROTECTED),
              f"{имя} защищён от перезаписи")


def test_release_installer_is_found_by_name() -> None:
    """Брать «первый попавшийся .exe» из релиза нельзя: рядом лежит сама
    программа одним файлом. Перепутать их — значит поставить не то."""
    print("\n[Установщик берётся по имени, а не первый попавшийся]")
    import json
    import io

    релиз = {
        "tag_name": "build-63",
        "assets": [
            {"name": "AI_Scalper_Pro.exe", "url": "u1", "size": 61000000},
            {"name": "AI_Scalper_Setup.exe", "url": "u2", "size": 46585820},
            {"name": "config.py", "url": "u3", "size": 100},
        ],
    }

    class Ответ(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    было = updater._request
    try:
        updater._request = lambda *a, **k: Ответ(json.dumps(релиз).encode())
        найдено = updater.latest_release_installer()
        check(найдено.get("url") == "u2",
              "Найден именно установщик", str(найдено.get("url")))
        check(найдено.get("size") == 46585820,
              "И его размер — для проверки, что файл не оборвался")
        check(найдено.get("tag") == "build-63", "И номер версии")

        # Программа одним файлом ищется отдельно и не путается с установщиком.
        updater._request = lambda *a, **k: Ответ(json.dumps(релиз).encode())
        check(updater.latest_release_exe().get("url") == "u1",
              "Сама программа берётся отдельно и своим именем")

        # Релиза без установщика быть не должно, но если он такой — не врём.
        пусто = {"tag_name": "build-64", "assets": [
            {"name": "config.py", "url": "u3", "size": 100}]}
        updater._request = lambda *a, **k: Ответ(json.dumps(пусто).encode())
        check(updater.latest_release_installer() == {},
              "Если установщика в релизе нет — так и сказано")
    finally:
        updater._request = было


def test_download_path_is_next_to_the_program() -> None:
    """Временную папку Windows чистит и система, и антивирус. Файл может
    исчезнуть между скачиванием и запуском — а запускать будет уже нечего:
    окна к тому моменту нет."""
    print("\n[Установщик кладётся рядом с программой, а не во временную папку]")
    было = updater.app_dir
    try:
        updater.app_dir = lambda: r"C:\prog"
        путь = updater.installer_download_path()
        check(путь.startswith(r"C:\prog"), "Рядом с программой", путь)
        check("temp" not in путь.lower() and "tmp" not in путь.lower(),
              "И не во временной папке", путь)
        check(путь.endswith(".new"),
              "С пометкой .new — чтобы не спутать с рабочим файлом")
    finally:
        updater.app_dir = было


if __name__ == "__main__":
    print("=" * 62)
    print("ТЕСТЫ: САМООБНОВЛЕНИЕ УСТАНОВКИ ПАПКОЙ")
    print("=" * 62)
    test_installer_is_recognised_correctly()
    test_install_script_does_the_right_things_in_the_right_order()
    test_install_refuses_when_file_is_missing_or_broken()
    test_folder_install_downloads_installer_instead_of_refusing()
    test_settings_are_never_touched_by_the_installer()
    test_protected_files_still_protected()
    test_release_installer_is_found_by_name()
    test_download_path_is_next_to_the_program()
    print()
    print("=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    sys.exit(1 if failed else 0)
