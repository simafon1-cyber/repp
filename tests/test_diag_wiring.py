#!/usr/bin/env python3
"""ДИАГНОСТИКА ДОХОДИТ ДО РЕПОЗИТОРИЯ, А НЕ ЛЕЖИТ МЁРТВЫМ КОДОМ.

ОТКУДА ЭТО

Владелец, дословно: «все ошибки, которые появляются, пускай он
записывает где-то в логе на гитхабе, чтобы ты его сам непосредственно мог
прочитать этот файл». Смысл — чтобы он перестал присылать снимки экрана.

СОБСТВЕННАЯ ОШИБКА, РАДИ КОТОРОЙ ЭТОТ ФАЙЛ И ПОЯВИЛСЯ

Модуль cloud_diag.py был написан целиком и покрыт тестами — но его НИКТО
НЕ ВЫЗЫВАЛ. Все проверки были зелёными, а состояние в репозиторий не
уезжало ни разу. Тест на функцию не отвечает на вопрос, работает ли она в
программе. Здесь проверяется именно это: окно собирается по-настоящему,
у него вызывается плановая выгрузка, и проверяется ФАКТ — текст
состояния действительно ушёл в отправку.

ЧЕГО ЭТОТ ТЕСТ НЕ ПРОВЕРЯЕТ

Что GitHub принял файл. Сети здесь нет, отправка подменена. И поведение у
настоящего брокера — брокера здесь тоже нет.

Запуск:  python3 tests/test_diag_wiring.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
APP = ROOT / "ai_scalper_standalone"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(BASE))

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


import run_baseline            # noqa: E402
import поддельный_tk as пт     # noqa: E402

for _имя, _модуль in пт.собрать_модули().items():
    sys.modules[_имя] = _модуль

import desktop_app as da       # noqa: E402
import cloud_diag              # noqa: E402
import cloud_journal           # noqa: E402


def собрать_окно():
    пт.сброс()
    окно = object.__new__(da.App)
    окно.root = sys.modules["tkinter"].Tk()
    окно._apply_theme()
    окно.stop_event = None
    окно.bot_thread = None
    окно._bot_should_run = False
    окно.tray_icon = None
    окно._dashboard_started = False
    окно.chat_history = []
    окно._auto_update_busy = False
    окно._start_bot_waits = 0
    окно._build_ui()
    return окно


def test_плановая_выгрузка_отправляет_состояние():
    print("\n[Состояние действительно уходит на отправку]")
    окно = собрать_окно()

    отправлено = {}
    готово = threading.Event()

    def поддельная_выгрузка(текст, снимок=None):
        отправлено["текст"] = текст
        отправлено["снимок"] = снимок
        готово.set()
        return True, "подделка"

    прежние = (cloud_diag.выгрузить, cloud_journal.enabled,
               cloud_journal.upload_if_due, cloud_journal.last_upload_ts)
    da.cloud_diag.выгрузить = поддельная_выгрузка
    da.cloud_journal.enabled = lambda: True
    da.cloud_journal.upload_if_due = lambda *a, **k: None
    da.cloud_journal.last_upload_ts = lambda: 0.0
    try:
        окно._journal_uploading = False
        окно._upload_journal_if_due()
        дождались = готово.wait(10.0)
    finally:
        (cloud_diag.выгрузить, cloud_journal.enabled,
         cloud_journal.upload_if_due, cloud_journal.last_upload_ts) = прежние
        da.cloud_diag.выгрузить = прежние[0]
        da.cloud_journal.enabled = прежние[1]
        da.cloud_journal.upload_if_due = прежние[2]
        da.cloud_journal.last_upload_ts = прежние[3]

    check(дождались, "Выгрузка состояния вызвана из программы",
          "не дождались вызова за 10 секунд")
    текст = отправлено.get("текст", "")
    check("СОСТОЯНИЕ ПРОГРАММЫ" in текст,
          "Отправлен именно текст состояния", текст[:60])
    check("СЧЁТ" in текст, "В нём есть раздел про счёт", текст[:60])


def test_выгрузка_не_включается_сама_по_себе():
    """Выключенная выгрузка обязана оставаться выключенной.

    Отправка состояния — это отправка данных наружу. Она не должна
    включаться от того, что кто-то починил соседний код."""
    print("\n[Выключено — значит выключено]")
    ок, почему = cloud_diag.выгрузить("СОСТОЯНИЕ ПРОГРАММЫ\nпроверка")
    check(not ок, "При выключенной выгрузке ничего не уходит", str(почему))
    check(bool(почему), "И названа причина", str(почему))


def test_ошибка_выгрузки_не_роняет_окно():
    """Диагностика не имеет права мешать работе программы."""
    print("\n[Сорвавшаяся выгрузка не ломает программу]")
    окно = собрать_окно()

    def падать(*a, **k):
        raise RuntimeError("сети нет")

    прежняя = da.cloud_diag.выгрузить
    прежние_ж = (da.cloud_journal.enabled, da.cloud_journal.upload_if_due,
                 da.cloud_journal.last_upload_ts)
    da.cloud_diag.выгрузить = падать
    da.cloud_journal.enabled = lambda: True
    da.cloud_journal.upload_if_due = lambda *a, **k: None
    da.cloud_journal.last_upload_ts = lambda: 0.0
    try:
        окно._journal_uploading = False
        окно._upload_journal_if_due()
        time.sleep(1.0)
        check(True, "Программа не упала из-за сорвавшейся выгрузки")
    except Exception as e:  # noqa: BLE001
        check(False, "Программа не упала из-за сорвавшейся выгрузки",
              f"{type(e).__name__}: {e}")
    finally:
        da.cloud_diag.выгрузить = прежняя
        (da.cloud_journal.enabled, da.cloud_journal.upload_if_due,
         da.cloud_journal.last_upload_ts) = прежние_ж


def main() -> int:
    print("=" * 70)
    print("ДИАГНОСТИКА: ДОХОДИТ ЛИ ОНА ДО РЕПОЗИТОРИЯ")
    print("=" * 70)
    for имя, ф in sorted(globals().items()):
        if имя.startswith("test_") and callable(ф):
            print(f"\n--- {имя}")
            try:
                ф()
            except Exception as e:  # noqa: BLE001
                check(False, f"{имя} доработала до конца",
                      f"{type(e).__name__}: {str(e).splitlines()[0]}")
    print("\n" + "=" * 70)
    print(f"Пройдено: {passed}   Сбоев: {failed}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
