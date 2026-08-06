#!/usr/bin/env python3
"""Тесты самообновления: программа скачивает и ставит новую версию сама.

Владелец: «Я хотел сделать синхронизацию, чтобы мне не надо было каждый раз
всё устанавливать, чтобы программа скачивала всё сама и изменяла данные
файлы».

Что здесь проверяется, по важности:
  1. ВАШИ ДАННЫЕ НЕ ТРОГАЮТСЯ. config.py, accounts.json, telegram_session,
     журналы и CSV сделок не могут быть перезаписаны обновлением ни при
     каких данных с сервера.
  2. ВСЁ ИЛИ НИЧЕГО. Если хоть один файл не скачался или скачался битым —
     не заменяется ни один. Половина новой версии хуже старой целиком.
  3. Ответ сети — не доверенные данные: путь вида ../../Windows не выведет
     запись за пределы папки программы (ни из дерева файлов, ни из архива).
  4. Скачанное действительно ПРИМЕНЯЕТСЯ: подмена .exe вызывается при старте,
     а не остаётся мёртвым кодом (так и было — файл лежал, программа
     запускалась старой).
  5. Обновление не ставится посреди работы под открытыми сделками.
  6. Токен GitHub не уезжает на чужой сервер при перенаправлении на хранилище.

Запуск:  python3 tests/test_selfupdate.py
"""

from __future__ import annotations

import ast
import io
import json
import os
import sys
import tempfile
import types
import urllib.error
import urllib.request
import zipfile
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
        print(f"  СБОЙ {name}" + (f"  -> {detail}" if detail else ""))


cfg = types.ModuleType("config")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg
CFG = cfg
CFG.UPDATE_ENABLED = True
CFG.UPDATE_REPO = "owner/repo"
CFG.UPDATE_BRANCH = "main"
CFG.UPDATE_TOKEN = "секретный-токен"

import updater as up   # noqa: E402


def code_only(text: str) -> str:
    """Файл без комментариев и строк документации: проверка не должна
    срабатывать на описание ошибки вместо самого кода."""
    out = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    doc = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            doc.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    for i, line in enumerate(text.splitlines(), start=1):
        if i in doc:
            continue
        out.append(line.split("#", 1)[0])
    return "\n".join(out)


# =====================================================================
# 1. Ваши данные не трогаются
# =====================================================================
def test_personal_files_never_updated() -> None:
    print("\n[Личные файлы обновление не трогает]")
    personal = ["config.py", "accounts.json", "telegram_session",
                "trades_log.csv", "learning_state.json", ".login_remember",
                "scalper.log"]
    for name in personal:
        check(name in up.PROTECTED, f"{name} в списке неприкосновенных")

    # Даже если сервер пришлёт их в списке файлов — они отсеются
    tree = [f"{up.PROGRAM_DIR}/{name}" for name in personal]
    tree += [f"{up.PROGRAM_DIR}/main.py", f"{up.PROGRAM_DIR}/config.py.example"]
    files = up.program_files(tree)
    names = [name for _, name in files]
    for name in personal:
        check(name not in names, f"Сервер прислал {name} — он всё равно отброшен")
    check("main.py" in names, "Обычный файл программы обновляется")
    check("config.py.example" in names,
          "Эталон настроек обновляется (из него берутся новые параметры)")


def test_only_program_folder() -> None:
    print("\n[Обновляется только папка программы]")
    tree = [
        f"{up.PROGRAM_DIR}/main.py",
        f"{up.PROGRAM_DIR}/sub/deep.py",     # вложенных папок у программы нет
        "tests/test_bridge.py",              # тесты пользователю не нужны
        "mql5/DualGuardEA.mq5",              # советники ставит другой механизм
        "README.md",
    ]
    names = [name for _, name in up.program_files(tree)]
    check(names == ["main.py"], "Берётся только своё", str(names))


def test_path_traversal_blocked() -> None:
    print("\n[Путь с сервера не может увести запись из папки программы]")
    for bad in ("../evil.py", "../../Windows/System32/x.py", "/etc/passwd",
                "C:/Windows/x.py", "a//b.py", "./x.py", "", "a/../../b.py"):
        check(up.safe_relative(bad) is False, f"Отклонён путь {bad!r}")
    for good in ("ai_scalper_standalone/main.py", "mql5/DualGuardEA.mq5"):
        check(up.safe_relative(good) is True, f"Обычный путь принят: {good}")

    tree = [f"../../../{up.PROGRAM_DIR}/evil.py",
            f"{up.PROGRAM_DIR}/../../../evil.py"]
    check(up.program_files(tree) == [],
          "Опасные пути не попадают в список обновления")


# =====================================================================
# 2. Всё или ничего
# =====================================================================
class FakeDownloads:
    """Подменяет download_text: отдаёт заготовленное содержимое или ошибку."""

    def __init__(self, contents: dict):
        self.contents = contents
        self.asked = []

    def __call__(self, path: str) -> str:
        self.asked.append(path)
        value = self.contents.get(path)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise urllib.error.HTTPError(path, 404, "Not Found", None, None)
        return value


def run_update(contents: dict, tree: list, target: str) -> dict:
    saved_download = up.download_text
    saved_list = up.list_repo_files
    saved_dir = up.app_dir
    up.download_text = FakeDownloads(contents)
    up.list_repo_files = lambda: tree
    up.app_dir = lambda: target
    try:
        return up.update_program_files()
    finally:
        up.download_text = saved_download
        up.list_repo_files = saved_list
        up.app_dir = saved_dir


def test_all_or_nothing() -> None:
    print("\n[Половина новой версии не ставится]")
    tree = [f"{up.PROGRAM_DIR}/a.py", f"{up.PROGRAM_DIR}/b.py",
            f"{up.PROGRAM_DIR}/c.py"]

    with tempfile.TemporaryDirectory() as d:
        for name in ("a.py", "b.py", "c.py"):
            Path(d, name).write_text(f"OLD = '{name}'\n", encoding="utf-8")

        # Один файл не скачался
        report = run_update({
            f"{up.PROGRAM_DIR}/a.py": "NEW = 1\n",
            f"{up.PROGRAM_DIR}/b.py": None,          # 404
            f"{up.PROGRAM_DIR}/c.py": "NEW = 3\n",
        }, tree, d)
        check(report["replaced"] == 0, "Ни один файл не заменён",
              str(report["replaced"]))
        check(any("не всё" in e for e in report["errors"]),
              "Сказано, почему установка отменена", str(report["errors"]))
        for name in ("a.py", "b.py", "c.py"):
            check(Path(d, name).read_text(encoding="utf-8").startswith("OLD"),
                  f"{name} остался старым")


def test_broken_download_rejected() -> None:
    print("\n[Битый файл не заменяет рабочий]")
    tree = [f"{up.PROGRAM_DIR}/a.py", f"{up.PROGRAM_DIR}/b.py"]
    with tempfile.TemporaryDirectory() as d:
        Path(d, "a.py").write_text("OLD = 1\n", encoding="utf-8")
        Path(d, "b.py").write_text("OLD = 2\n", encoding="utf-8")

        report = run_update({
            f"{up.PROGRAM_DIR}/a.py": "NEW = 1\n",
            f"{up.PROGRAM_DIR}/b.py": "def сломано(:\n",   # не Python
        }, tree, d)
        check(report["replaced"] == 0, "Ничего не заменено")
        check(any("битый" in e for e in report["errors"]),
              "Сказано, что файл битый", str(report["errors"]))
        check(Path(d, "a.py").read_text(encoding="utf-8") == "OLD = 1\n",
              "Соседний файл тоже не пострадал")


def test_successful_update() -> None:
    print("\n[Удачное обновление меняет файлы и делает копию]")
    tree = [f"{up.PROGRAM_DIR}/a.py", f"{up.PROGRAM_DIR}/b.py"]
    with tempfile.TemporaryDirectory() as d:
        Path(d, "a.py").write_text("OLD = 1\n", encoding="utf-8")
        Path(d, "b.py").write_text("SAME = 2\n", encoding="utf-8")
        Path(d, "config.py").write_text("MY_SECRET = 'мой пароль'\n", encoding="utf-8")

        report = run_update({
            f"{up.PROGRAM_DIR}/a.py": "NEW = 1\n",
            f"{up.PROGRAM_DIR}/b.py": "SAME = 2\n",       # не изменился
        }, tree, d)
        check(not report["errors"], "Ошибок нет", str(report["errors"]))
        check(report["downloaded"] == 2, "Скачаны оба файла")
        check(report["replaced"] == 1, "Заменён только изменившийся",
              str(report["replaced"]))
        check(Path(d, "a.py").read_text(encoding="utf-8") == "NEW = 1\n",
              "Новый код на месте")
        check(Path(d, "a.py.bak").exists(),
              "Старая версия сохранена рядом — есть куда откатиться")
        check(not Path(d, "b.py.bak").exists(),
              "Неизменившийся файл не копируется зря")
        check(Path(d, "config.py").read_text(encoding="utf-8")
              == "MY_SECRET = 'мой пароль'\n",
              "Ваш config.py остался нетронутым")
        check(report["restart_needed"] is True,
              "Сказано, что нужен перезапуск")


def test_empty_repo_is_explained() -> None:
    print("\n[Пустой ответ объясняется, а не молчит]")
    with tempfile.TemporaryDirectory() as d:
        report = run_update({}, ["README.md"], d)
        check(report["replaced"] == 0, "Ничего не заменено")
        check(any(up.PROGRAM_DIR in e for e in report["errors"]),
              "Сказано, что папка программы не найдена", str(report["errors"]))


# =====================================================================
# 3. Архив сборки
# =====================================================================
def test_zip_slip_blocked() -> None:
    print("\n[Архив сборки не может записать файл куда попало]")
    with tempfile.TemporaryDirectory() as d:
        evil = os.path.join(d, "evil.zip")
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("../../../подсунутый.exe", b"MZ")
        target = os.path.join(d, "out.exe")
        check(up._extract_exe(evil, target) is False,
              "Файл с путём наружу не распакован")
        check(not os.path.exists(target), "Ничего не записано")
        check(not os.path.exists(os.path.join(d, "подсунутый.exe")),
              "И рядом тоже ничего не появилось")

        good = os.path.join(d, "good.zip")
        with zipfile.ZipFile(good, "w") as z:
            z.writestr("AI_Scalper_Pro.exe", "MZ-программа".encode("utf-8"))
        check(up._extract_exe(good, target) is True, "Обычный архив распакован")
        check(open(target, "rb").read() == "MZ-программа".encode("utf-8"), "Содержимое верное")

        # В архиве сборки рядом лежит установщик. Взять его вместо программы —
        # значит при следующем запуске открыть окно установки вместо торговли.
        both = os.path.join(d, "both.zip")
        with zipfile.ZipFile(both, "w") as z:
            z.writestr("AI_Scalper_Setup.exe", "УСТАНОВЩИК".encode("utf-8"))
            z.writestr("AI_Scalper_Pro.exe", "ПРОГРАММА".encode("utf-8"))
        check(up._extract_exe(both, target) is True, "Архив с двумя .exe распакован")
        check(open(target, "rb").read() == "ПРОГРАММА".encode("utf-8"),
              "Выбрана программа, а не установщик")


# =====================================================================
# 4. Скачанное действительно применяется
# =====================================================================
def test_swap_is_actually_called() -> None:
    print("\n[Скачанная версия действительно ставится при запуске]")
    body = code_only((APP / "desktop_app.py").read_text(encoding="utf-8"))
    check("updater.apply_pending_swap()" in body,
          "Подмена скачанного файла вызывается программой")

    start = body.split("def main(", 1)[1]
    swap_at = start.find("apply_pending_swap")
    app_at = start.find("App()")
    check(0 <= swap_at < app_at,
          "Подмена происходит ДО запуска окна — позже файл уже занят")


def test_swap_retries_and_backs_up() -> None:
    print("\n[Подмена переживает «файл занят» и оставляет откат]")
    with tempfile.TemporaryDirectory() as d:
        current = os.path.join(d, "AI_Scalper_Pro.exe")
        Path(current).write_bytes("СТАРАЯ".encode("utf-8"))
        Path(current + ".new").write_bytes("НОВАЯ".encode("utf-8"))

        saved_frozen = getattr(sys, "frozen", None)
        saved_exe = sys.executable
        sys.frozen = True
        sys.executable = current
        try:
            text = up.apply_pending_swap(attempts=3, pause=0.01)
        finally:
            sys.executable = saved_exe
            if saved_frozen is None:
                del sys.frozen
            else:
                sys.frozen = saved_frozen

        check("обновлена" in text, "Подмена удалась", text)
        check(open(current, "rb").read() == "НОВАЯ".encode("utf-8"), "На месте новая версия")
        check(open(current + ".old", "rb").read() == "СТАРАЯ".encode("utf-8"),
              "Старая сохранена — есть куда вернуться")
        check(not os.path.exists(current + ".new"),
              "Временный файл убран, повторной подмены не будет")

    # Без скачанного файла подмена молчит
    saved_frozen = getattr(sys, "frozen", None)
    sys.frozen = False
    try:
        check(up.apply_pending_swap() == "", "Нечего ставить — ничего не делается")
    finally:
        if saved_frozen is None:
            del sys.frozen
        else:
            sys.frozen = saved_frozen


def test_restart_after_update() -> None:
    print("\n[После обновления программа перезапускается сама]")
    body = code_only((APP / "updater.py").read_text(encoding="utf-8"))
    check("def restart_program" in body, "Перезапуск есть")
    fn = body.split("def restart_program", 1)[1].split("\ndef ", 1)[0]
    check("subprocess.Popen" in fn, "Новая копия запускается")
    check("os._exit(0)" in fn,
          "Старая завершается жёстко — иначе работали бы две копии сразу")

    ui = code_only((APP / "desktop_app.py").read_text(encoding="utf-8"))
    after = ui.split("def _after_auto_update", 1)[1].split("\n    def _bot_is_busy", 1)[0]
    check("restart_needed" in after,
          "Перезапуск только когда файлы реально менялись")


# =====================================================================
# 5. Не посреди работы
# =====================================================================
def test_not_during_open_positions() -> None:
    print("\n[Обновление не ставится под открытыми сделками]")
    ui = code_only((APP / "desktop_app.py").read_text(encoding="utf-8"))
    check("def _bot_is_busy" in ui, "Есть проверка занятости бота")
    fn = ui.split("def _bot_is_busy", 1)[1].split("\n    def ", 1)[0]
    check("positions" in fn, "Смотрятся именно открытые позиции")

    manual = ui.split("def update_everything_now", 1)[1].split("\n    def ", 1)[0]
    check("_bot_is_busy" in manual,
          "Кнопка «Обновить всё» спрашивает при открытых сделках")

    # Автоустановка — только при запуске, когда торговля ещё не началась
    init = ui.split("def __init__", 1)[1].split("\n    def ", 1)[0]
    check("UPDATE_AUTO_APPLY" in init,
          "Автоустановка привязана к запуску программы")
    check("_start_bot_when_ready" in init,
          "Торговля стартует только после обновления")

    gate = ui.split("def _start_bot_when_ready", 1)[1].split("\n    def ", 1)[0]
    check("START_BOT_MAX_WAIT_TICKS" in gate,
          "Зависшее обновление не блокирует торговлю навсегда")


def test_auto_apply_off_by_default() -> None:
    """Проверка обновления включена "из коробки" (UPDATE_ENABLED=True,
    UPDATE_REPO заполнен) — программа всегда обновляется сама собой из того
    репозитория, откуда приехала, вписывать ничего не нужно. Это безопасно,
    потому что означает только "проверить", а не "поставить без спроса": для
    установки нужна ЛИБО отдельная галочка UPDATE_AUTO_APPLY (по умолчанию
    выключена), ЛИБО явное согласие в диалоге."""
    print("\n[Проверка включена по умолчанию, установка — только с согласия]")
    fresh = types.ModuleType("fresh")
    exec((APP / "config.py.example").read_text(encoding="utf-8"), fresh.__dict__)
    check(fresh.UPDATE_ENABLED is True,
          "Проверка обновлений включена по умолчанию")
    check(fresh.UPDATE_REPO == "simafon1-cyber/repp",
          "Репозиторий заполнен по умолчанию — свой репозиторий программы",
          fresh.UPDATE_REPO)
    check(fresh.UPDATE_BRANCH == "",
          "Ветка по умолчанию пустая — определяется автоматически",
          fresh.UPDATE_BRANCH)
    check(fresh.UPDATE_AUTO_APPLY is False,
          "Автоустановка выключена по умолчанию — включённой проверки для этого мало")
    check(fresh.UPDATE_REQUEST_BUILD is False,
          "Заказ сборки выключен по умолчанию")


# =====================================================================
# 6. Токен не уезжает на чужой сервер
# =====================================================================
class FakeResponse:
    def __init__(self, url):
        self.url = url


def test_locked_token_is_never_sent() -> None:
    """Реальная жалоба владельца: «Нет доступа к репозиторию. Для закрытого
    нужен токен GitHub» — при том, что токен у него как раз БЫЛ вписан.

    Причина: по умолчанию REQUIRE_LOGIN=False, программа открывается без
    пароля входа, и secure_store.unlock_config() не вызывается вовсе. Токен
    остаётся в памяти строкой "enc:gAAAAAB..." — и ровно в таком виде уходил
    в заголовок Authorization. GitHub отвечал 401, а текст ошибки уводил в
    противоположную сторону: «нужен токен», хотя нужен был не токен, а
    пароль входа."""
    print("\n[Зашифрованный токен не уходит в GitHub]")
    import secure_store as ss

    saved = CFG.UPDATE_TOKEN
    try:
        CFG.UPDATE_TOKEN = ss.encrypt_value("настоящий-токен", "пароль", "aabb")
        check(ss.is_locked(CFG.UPDATE_TOKEN) is True,
              "Строка опознана как ещё зашифрованная")
        check(up.token_locked() is True, "Обновление видит, что токен недоступен")
        check(up.token() == "",
              "Наружу отдаётся ПУСТО, а не зашифрованная строка", repr(up.token()))

        hint = up.auth_hint()
        check("не расшифрован" in hint,
              "Причина названа настоящая, а не «нужен токен»", hint)
        check("парол" in hint.lower(), "Сказано, что делать", hint)

        # Обычный (расшифрованный) токен по-прежнему используется как есть
        CFG.UPDATE_TOKEN = "github_pat_ОБЫЧНЫЙ"
        check(up.token_locked() is False, "Обычный токен не считается запертым")
        check(up.token() == "github_pat_ОБЫЧНЫЙ", "И передаётся как есть")
    finally:
        CFG.UPDATE_TOKEN = saved


def test_bad_token_falls_back_to_no_token() -> None:
    """Открытый репозиторий читается вообще без прав, но НЕВЕРНЫЙ заголовок
    Authorization ломает даже его. Значит одна испорченная настройка (истёкший
    токен, опечатка, нерасшифрованная строка) намертво останавливала
    обновление, которому токен был не нужен. Проверяем запасной путь."""
    print("\n[Непринятый токен не блокирует открытый репозиторий]")

    class _Json:
        """Мини-заглушка ответа urllib: `with _request(...) as r: r.read()`."""

        def __init__(self, payload):
            self._body = json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self._body

    saved_open = up._open
    saved_token = CFG.UPDATE_TOKEN
    up._token_ignored["value"] = False
    try:
        CFG.UPDATE_TOKEN = "истёкший-токен"
        attempts = []

        def fake(url, accept, timeout, use_token):
            attempts.append(use_token)
            if use_token:
                raise urllib.error.HTTPError(url, 401, "Bad credentials", None, None)
            return _Json({"default_branch": "рабочая-ветка"})

        up._open = fake
        data = b""
        try:
            with up._request("https://api.github.com/repos/o/r") as response:
                data = response.read()
        except urllib.error.HTTPError as e:
            check(False, "Ответ всё-таки получен",
                  f"вместо запасной попытки без токена пришла ошибка {e.code}")
        check(b"default_branch" in data, "Ответ всё-таки получен")
        check(attempts == [True, False],
              "Сначала с токеном, потом без него — именно в таком порядке",
              str(attempts))
        check(up.token_was_ignored() is True,
              "Факт игнорирования токена запомнен — покажем человеку")

        # ЗАКРЫТЫЙ репозиторий: без токена тоже отказ -> отдаём ИСХОДНУЮ причину
        up._token_ignored["value"] = False

        def fake_private(url, accept, timeout, use_token):
            raise urllib.error.HTTPError(url, 401 if use_token else 404,
                                         "no", None, None)

        up._open = fake_private
        try:
            up._request("https://api.github.com/repos/o/r")
            check(False, "Закрытый репозиторий обязан вернуть ошибку")
        except urllib.error.HTTPError as e:
            check(e.code == 401,
                  "Отдана исходная ошибка про права (401), а не 404 от второй попытки",
                  str(e.code))
        check(up.token_was_ignored() is False,
              "Ложного «токен не нужен» при закрытом репозитории нет")

        # Без токена вовсе второй попытки быть не должно
        CFG.UPDATE_TOKEN = ""
        attempts.clear()

        def fake_404(url, accept, timeout, use_token):
            attempts.append(use_token)
            raise urllib.error.HTTPError(url, 403, "no", None, None)

        up._open = fake_404
        try:
            up._request("https://api.github.com/repos/o/r")
        except urllib.error.HTTPError:
            pass
        check(attempts == [True],
              "Токена нет — повторять нечего, лишнего запроса не делаем",
              str(attempts))
    finally:
        up._open = saved_open
        CFG.UPDATE_TOKEN = saved_token
        up._token_ignored["value"] = False


def test_journal_reports_locked_token() -> None:
    """У журнала запасного пути нет — запись в репозиторий требует прав всегда.
    Значит он обязан хотя бы назвать НАСТОЯЩУЮ причину."""
    print("\n[Журнал в облаке объясняет запертый токен]")
    import cloud_journal as cj
    import secure_store as ss

    saved = (CFG.JOURNAL_CLOUD_ENABLED, CFG.JOURNAL_REPO, CFG.JOURNAL_TOKEN)
    try:
        CFG.JOURNAL_CLOUD_ENABLED = True
        CFG.JOURNAL_REPO = "owner/repo"
        CFG.JOURNAL_TOKEN = ss.encrypt_value("токен-записи", "пароль", "aabb")
        check(cj.token_locked() is True, "Журнал видит запертый токен")
        check(cj.token() == "", "И не отправляет зашифрованную строку")
        ok, reason = cj.ready()
        check(ok is False, "Выгрузка не начинается")
        check("не расшифрован" in reason,
              "Причина настоящая, а не «токен не указан»", reason)
    finally:
        (CFG.JOURNAL_CLOUD_ENABLED, CFG.JOURNAL_REPO, CFG.JOURNAL_TOKEN) = saved


def test_locked_fields_are_listed() -> None:
    print("\n[Программа знает полный список недоступных секретов]")
    import secure_store as ss

    probe = types.ModuleType("probe")
    probe.SECURITY_SALT = "aabb"
    enc = ss.encrypt_value("x", "пароль", "aabb")
    probe.MT5_PASSWORD = enc
    probe.UPDATE_TOKEN = enc
    probe.ANTHROPIC_API_KEY = "обычный-ключ"
    probe.NEWS_API_KEYS = {"finnhub": enc, "other": "открытый"}

    locked = ss.locked_fields(probe)
    check("MT5_PASSWORD" in locked, "Пароль MT5 попал в список")
    check("UPDATE_TOKEN" in locked, "Токен обновления попал в список")
    check("ANTHROPIC_API_KEY" not in locked, "Расшифрованный ключ не попал")
    check("NEWS_API_KEYS[finnhub]" in locked, "Ключ новостей внутри словаря найден")
    check("NEWS_API_KEYS[other]" not in locked, "Открытый ключ новостей не попал")
    check(ss.locked_fields(types.ModuleType("empty")) == [],
          "Пустой конфиг — пустой список")


def test_token_not_sent_to_storage() -> None:
    print("\n[Токен GitHub не уходит на сторонний сервер]")
    handler = up._DropAuthOnRedirect()

    request = urllib.request.Request(
        "https://api.github.com/repos/owner/repo/releases/assets/1",
        headers={"Authorization": "Bearer секретный-токен",
                 "Accept": "application/octet-stream"})
    moved = handler.redirect_request(
        request, io.BytesIO(b""), 302, "Found", {},
        "https://objects.githubusercontent.com/подписанная-ссылка")
    check(moved is not None, "Перенаправление обработано")
    if moved is not None:
        headers = {k.lower(): v for k, v in moved.headers.items()}
        check("authorization" not in headers,
              "На чужом адресе заголовка с токеном нет", str(headers))
        check("Authorization" not in moved.unredirected_hdrs,
              "И в скрытых заголовках тоже")

    # На своём адресе токен остаётся — иначе закрытый репозиторий не откроется
    same = handler.redirect_request(
        urllib.request.Request(
            "https://api.github.com/a",
            headers={"Authorization": "Bearer секретный-токен"}),
        io.BytesIO(b""), 302, "Found", {}, "https://api.github.com/b")
    if same is not None:
        headers = {k.lower(): v for k, v in same.headers.items()}
        check("authorization" in headers,
              "На том же сервере токен сохраняется")


# =====================================================================
# 7. Заказ сборки
# =====================================================================
def test_build_request_needs_token() -> None:
    print("\n[Заказ сборки объясняет, чего не хватает]")
    saved = CFG.UPDATE_TOKEN
    CFG.UPDATE_TOKEN = ""
    problem = up.request_build()
    check("токен" in problem.lower(), "Без токена сказано прямо", problem)
    check("Actions" in problem, "Названо нужное право", problem)
    CFG.UPDATE_TOKEN = saved

    body = code_only((APP / "updater.py").read_text(encoding="utf-8"))
    check("workflow_dispatch" in (APP.parent / ".github" / "workflows"
                                  / up.BUILD_WORKFLOW).read_text(encoding="utf-8"),
          "Сценарий сборки разрешает запуск по команде")
    check("dispatches" in body, "Сборка заказывается через GitHub API")


def test_artifact_download_explains_real_reason() -> None:
    """Жалоба владельца со снимка экрана: советники обновились, а программа
    ответила «Нет доступа к репозиторию. Для закрытого нужен токен».

    Репозиторий у него ОТКРЫТЫЙ. Проверено вживую: список сборок в Actions
    отдаётся без токена (200), а сам файл сборки — только по токену (403).
    Это правило GitHub, а не признак закрытого репозитория. Прежний текст
    отправлял заводить токен там, где хватает одного релиза."""
    print("\n[Отказ по сборке .exe объяснён по-настоящему]")

    saved_release = up.latest_release_exe
    saved_artifact = up.latest_build_artifact
    saved_download = up.download_binary

    def denied(url, destination, accept, progress=None):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    up.latest_release_exe = lambda: {}          # релизов ещё нет
    up.latest_build_artifact = lambda: {"url": "https://api/artifacts/1/zip",
                                        "name": up.ARTIFACT_NAME,
                                        "created": "2026-08-03"}
    up.download_binary = denied
    try:
        result = up.download_new_exe()
        text = result.get("error", "")
        check(not result.get("ok"), "Сборка не считается установленной")
        check("закрыт" not in text.lower(),
              "Открытый репозиторий больше не называют закрытым", text)
        check("Actions" in text, "Сказано, ГДЕ лежит сборка", text)
        check("v1.0" in text or "релиз" in text.lower(),
              "Назван путь без токена — выпустить релиз", text)
    finally:
        up.latest_release_exe = saved_release
        up.latest_build_artifact = saved_artifact
        up.download_binary = saved_download

    # Совсем нет сборок — тоже сначала предлагаем релиз, а не токен
    up.latest_release_exe = lambda: {}
    up.latest_build_artifact = lambda: {}
    try:
        text = up.download_new_exe().get("error", "")
        check("релиз" in text.lower() or "v1.0" in text,
              "Когда сборок нет — тоже советуем релиз", text)
    finally:
        up.latest_release_exe = saved_release
        up.latest_build_artifact = saved_artifact

    # Релиз скачивается обычной ссылкой, без токена: это и есть основной путь
    body = code_only((APP / "updater.py").read_text(encoding="utf-8"))
    fn = body.split("def download_new_exe", 1)[1].split("\ndef ", 1)[0]
    check(fn.index("latest_release_exe") < fn.index("latest_build_artifact"),
          "Релиз пробуется РАНЬШЕ сборки из Actions")

    # И релиз обязан появляться от ЛЮБОЙ сборки, а не только от тега v*:
    # иначе кнопка «Собрать новую версию» кладёт результат в артефакты,
    # откуда программа его без токена не достанет — тот самый тупик
    workflow = (APP.parent / ".github" / "workflows"
                / up.BUILD_WORKFLOW).read_text(encoding="utf-8")
    release_step = workflow.split("Опубликовать в Releases", 1)
    check(len(release_step) == 2, "Шаг публикации релиза есть")
    if len(release_step) == 2:
        step = release_step[1]
        check("if: startsWith(github.ref, 'refs/tags/v')\n" not in step,
              "Релиз выпускается не только по тегу v*")
        check("tag_name" in step, "Имя тега задаётся для любой сборки")
        check("contents: write" in workflow,
              "У сборки есть право выложить файл в Releases")


def test_version_is_visible() -> None:
    """Владелец: «прописывай где-то версию, чтобы было видно, обновилось или
    нет». Несколько раз выходило так, что исправление давно выпущено, а
    запущена старая версия, и мы искали ошибку, которой в новой уже нет."""
    print("\n[Версия видна в программе]")

    sys.path.insert(0, str(APP))
    import stamp_version
    import version as v

    # В исходниках честно написано «разработка»: такой запуск и правда не
    # является выпущенной сборкой
    check(v.is_release() is False, "Запуск из исходников не выдаёт себя за сборку")
    check("разработка" in v.short(), "Так и написано", v.short())
    check(v.number() == 0, "Номера сборки нет")

    # Сборочный сценарий вписывает настоящие значения
    stamped = stamp_version.stamp((APP / "version.py").read_text(encoding="utf-8"),
                                  "11", "92a52c7abcdef", "2026-08-06")
    module = types.ModuleType("version_stamped")
    exec(stamped, module.__dict__)
    check(module.is_release() is True, "После сборки это выпущенная версия")
    check(module.short() == "сборка 11", "Коротко для заголовка окна", module.short())
    check(module.number() == 11, "Номер сборки читается числом")
    full = module.full()
    for part in ("сборка 11", "2026-08-06", "92a52c7"):
        check(part in full, f"В подробной строке есть «{part}»", full)
    check("92a52c7abcdef" not in full, "Номер правки укорочен до 7 знаков", full)

    # Испорченный version.py должен ломать СБОРКУ, а не давать .exe без версии
    broken = False
    try:
        stamp_version.stamp("НЕ ТОТ ФАЙЛ", "1", "a", "b")
    except ValueError:
        broken = True
    check(broken, "Если version.py изменён — сборка падает, а не молчит")

    # Версия показывается в заголовке окна и на вкладке «Система»
    ui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    check("app_version.short()" in ui.split("self.root.title", 1)[1][:120],
          "Версия стоит в заголовке окна")
    check("self.version_var" in ui, "На вкладке «Система» есть строка версии")
    check("_refresh_version_line" in ui,
          "После проверки обновлений строка версии обновляется")

    # Сравнение с GitHub: моя сборка последняя или нет
    saved = up.latest_release_build
    saved_version = up.app_version
    try:
        up.app_version = module              # как будто установлена сборка 11
        up.latest_release_build = lambda: 11
        check("последняя" in up.version_status(),
              "Сборка совпадает с GitHub — сказано, что обновлять нечего",
              up.version_status())
        up.latest_release_build = lambda: 14
        text = up.version_status()
        check("14" in text and "новее" in text,
              "Есть более новая сборка — названа её номером", text)
        up.latest_release_build = lambda: 0
        check("не удалось" in up.version_status(),
              "Сети нет — версия всё равно показана", up.version_status())
    finally:
        up.latest_release_build = saved
        up.app_version = saved_version

    # Сценарий сборки действительно вписывает версию, а не забыл про неё
    workflow = (APP.parent / ".github" / "workflows"
                / up.BUILD_WORKFLOW).read_text(encoding="utf-8")
    check("stamp_version.py" in workflow, "Сборка вписывает версию в программу")
    check("--hidden-import version" in workflow,
          "Модуль версии попадает внутрь .exe")

    # Живой отказ: сборка 11 упала на UnicodeEncodeError — консоль Windows на
    # серверах GitHub работает в cp1252, и кириллица в выводе роняет весь шаг.
    stamp_src = (APP / "stamp_version.py").read_text(encoding="utf-8")
    printed = [ln for ln in stamp_src.splitlines()
               if ln.strip().startswith("print(")]
    check(printed, "В stamp_version.py есть вывод (проверка теста)")
    for line in printed:
        try:
            line.encode("cp1252")
            ok = True
        except UnicodeEncodeError:
            ok = False
        check(ok, "Вывод сборки не содержит кириллицы", line.strip())
    # Текст исключения тоже уходит в консоль сборки — трассировка печатается
    # тем же кодировщиком, и кириллица в ней снова уронила бы шаг
    raises = [ln for ln in stamp_src.splitlines() if "raise " in ln]
    for line in raises:
        try:
            line.encode("cp1252")
            ok = True
        except UnicodeEncodeError:
            ok = False
        check(ok, "Текст ошибки сборки тоже без кириллицы", line.strip())
    step = workflow.split("Прописать версию", 1)[1].split("- name:", 1)[0]
    check("PYTHONUTF8" in step,
          "Шаг сборки переведён в UTF-8 — иначе русский текст роняет сборку")


def test_update_everything_covers_both() -> None:
    print("\n[Одна кнопка обновляет и советники, и программу]")
    body = code_only((APP / "updater.py").read_text(encoding="utf-8"))
    fn = body.split("def update_everything", 1)[1].split("\ndef ", 1)[0]
    check("update_advisors" in fn, "Советники в MetaTrader обновляются")
    check("download_new_exe" in fn, "Собранная программа скачивается")
    check("update_program_files" in fn, "Файлы исходников обновляются")
    check("is_frozen()" in fn,
          "Способ выбирается по тому, как программа запущена")

    ui = code_only((APP / "desktop_app.py").read_text(encoding="utf-8"))
    manual = ui.split("def _after_update_everything", 1)[1].split("\n    def ", 1)[0]
    check("config_migrate.sync()" in manual,
          "Новые настройки дописываются тем же нажатием")


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ САМООБНОВЛЕНИЯ: ПРОГРАММА СТАВИТ НОВУЮ ВЕРСИЮ САМА")
    print("=" * 62)

    test_personal_files_never_updated()
    test_only_program_folder()
    test_path_traversal_blocked()

    test_all_or_nothing()
    test_broken_download_rejected()
    test_successful_update()
    test_empty_repo_is_explained()

    test_zip_slip_blocked()

    test_swap_is_actually_called()
    test_swap_retries_and_backs_up()
    test_restart_after_update()

    test_not_during_open_positions()
    test_auto_apply_off_by_default()

    test_locked_token_is_never_sent()
    test_bad_token_falls_back_to_no_token()
    test_journal_reports_locked_token()
    test_locked_fields_are_listed()
    test_token_not_sent_to_storage()

    test_build_request_needs_token()
    test_artifact_download_explains_real_reason()
    test_version_is_visible()
    test_update_everything_covers_both()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
