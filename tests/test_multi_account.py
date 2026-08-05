#!/usr/bin/env python3
"""Тесты многосчётности: хранение счетов, шифрование, группировка процессов.

Запуск:  python3 tests/test_multi_account.py
MetaTrader 5 не нужен — процессы подменяются заглушками.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent
APP = BASE.parent / "ai_scalper_standalone"
sys.path.insert(0, str(APP))

# accounts_backup.py тянет cloud_journal.py, а тот — config: настоящего
# config.py в git нет (там ключи), подставляем эталон, как это делают
# остальные тесты.
_cfg = types.ModuleType("config")
exec((APP / "config.py.example").read_text(encoding="utf-8"), _cfg.__dict__)
sys.modules["config"] = _cfg

import accounts as acc_mod  # noqa: E402
from account_supervisor import AccountState, AccountSupervisor  # noqa: E402
from accounts import (MIN_POLL_MS, Account, AccountStore, migrate_from_config,  # noqa: E402
                      resolve_symbol, resolve_symbols)
import accounts_backup as ab  # noqa: E402
import cloud_journal as cj    # noqa: E402

PASSWORD = "пароль-входа"
SALT = "a0d1491a207ae9ecb87b775e065f2fba"

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


def make(login=1001, **kw) -> Account:
    data = dict(name=f"Счёт {login}", login=login, server="Broker-Demo",
                password="торговый-пароль")
    data.update(kw)
    return Account(**data)


def test_validation() -> None:
    print("\n=== 1. Проверка данных счёта ===")
    check(make().validate() == [], "заполненный счёт проходит проверку")
    check(make(login=0).validate() != [], "без номера счёта — ошибка")
    check(make(password="").validate() != [], "без пароля — ошибка")
    check(make(server="").validate() != [], "без сервера — ошибка")
    check(make(risk_percent=0).validate() != [], "нулевой риск — ошибка")
    check(make(risk_percent=50).validate() != [], "риск 50% — ошибка")
    check(make(max_positions=0).validate() != [], "ноль позиций — ошибка")
    check(make(poll_interval_ms=1).validate() != [],
          f"интервал меньше {MIN_POLL_MS} мс — ошибка")
    check(make(poll_interval_ms=MIN_POLL_MS).validate() == [],
          f"интервал ровно {MIN_POLL_MS} мс — допустим")


def test_parallel_flag() -> None:
    print("\n=== 2. Параллельный или очередной режим ===")
    check(not make().runs_in_parallel(), "без своего терминала — по очереди")
    check(make(terminal_path="C:/mt5-a/terminal64.exe").runs_in_parallel(),
          "со своей копией терминала — параллельно")


def test_store_encryption() -> None:
    print("\n=== 3. Пароли на диске зашифрованы ===")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "accounts.json"
        store = AccountStore(path)
        check(store.load(PASSWORD, SALT) == [], "нет файла — пустой список, без ошибки")

        store.add(make(1001, password="секрет-один"), PASSWORD, SALT)
        store.add(make(1002, password="секрет-два"), PASSWORD, SALT)

        raw = path.read_text(encoding="utf-8")
        check("секрет-один" not in raw, "первый пароль не виден в файле")
        check("секрет-два" not in raw, "второй пароль не виден в файле")
        check("enc:" in raw, "пароли записаны в зашифрованном виде")
        data = json.loads(raw)
        check("password" not in data["accounts"][0], "открытого поля password нет")

        again = AccountStore(path)
        loaded = again.load(PASSWORD, SALT)
        check(len(loaded) == 2, "оба счёта прочитаны")
        check(loaded[0].password == "секрет-один", "первый пароль расшифрован")
        check(loaded[1].password == "секрет-два", "второй пароль расшифрован")

        # Неверный пароль входа не должен ронять программу и выдавать секреты
        wrong = AccountStore(path).load("другой-пароль", SALT)
        check(len(wrong) == 2, "при неверном пароле счета всё равно видны")
        check(all(a.password == "" for a in wrong),
              "при неверном пароле пароли счетов НЕ выдаются",
              str([a.password for a in wrong]))

        # Испорченный файл
        path.write_text("{не json", encoding="utf-8")
        check(AccountStore(path).load(PASSWORD, SALT) == [],
              "испорченный файл не роняет программу")


def test_store_operations() -> None:
    print("\n=== 4. Операции со списком счетов ===")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "accounts.json"
        store = AccountStore(path)
        store.add(make(1), PASSWORD, SALT)
        store.add(make(2, enabled=False), PASSWORD, SALT)
        store.add(make(3, password=""), PASSWORD, SALT)

        check(store.find(2) is not None, "поиск по номеру работает")
        check(store.find(99) is None, "несуществующий счёт не находится")

        ready = [a.login for a in store.ready_accounts()]
        check(ready == [1], "к запуску берётся только включённый и настроенный",
              str(ready))

        changed = make(1, name="Переименован")
        store.replace(1, changed, PASSWORD, SALT)
        check(AccountStore(path).load(PASSWORD, SALT)[0].name == "Переименован",
              "изменение счёта сохраняется на диск")

        check(store.remove(2, PASSWORD, SALT), "счёт удаляется")
        check(not store.remove(2, PASSWORD, SALT), "повторное удаление возвращает False")
        check(len(AccountStore(path).load(PASSWORD, SALT)) == 2, "удаление сохранено")


def test_accounts_file_survives_restart_in_exe() -> None:
    """Жалоба владельца: «счета которые я добавляю все время сохранялись» —
    добавленные счета пропадали при перезапуске.

    Причина: путь к accounts.json брался как Path(__file__).parent —папка
    САМОГО МОДУЛЯ. В onefile-сборке PyInstaller распаковывает модули во
    ВРЕМЕННУЮ папку (sys._MEIPASS) и удаляет её при выходе. Значит файл со
    счетами писался туда же и исчезал вместе с ней при каждом закрытии
    программы."""
    print("\n=== 3д. Файл счетов переживает перезапуск .exe ===")
    import importlib

    saved_frozen = getattr(sys, "frozen", None)
    saved_exe = sys.executable
    try:
        sys.frozen = True
        sys.executable = "/opt/AI_Scalper/AI_Scalper_Pro.exe"
        importlib.reload(acc_mod)
        where = str(acc_mod.app_dir())
        check(where == "/opt/AI_Scalper",
              "В собранном .exe файл счетов лежит РЯДОМ С EXE", where)
        check("MEI" not in where and "emp" not in where.replace("/opt", ""),
              "И точно не во временной папке PyInstaller", where)
        check(str(acc_mod.ACCOUNTS_FILE) == "/opt/AI_Scalper/accounts.json",
              "Полный путь к accounts.json верный", str(acc_mod.ACCOUNTS_FILE))
    finally:
        if saved_frozen is None:
            del sys.frozen
        else:
            sys.frozen = saved_frozen
        sys.executable = saved_exe
        importlib.reload(acc_mod)

    check(str(acc_mod.app_dir()).endswith("ai_scalper_standalone"),
          "При запуске из исходников путь прежний — рядом с модулем",
          str(acc_mod.app_dir()))


def test_delete_button_exists() -> None:
    """Владелец просил кнопку удаления счёта — проверяем, что она есть и
    реально удаляет из файла, а не только со экрана."""
    print("\n=== 3е. Кнопка «Удалить» есть и работает ===")
    ui = (APP / "accounts_tab.py").read_text(encoding="utf-8")
    check('"Удалить"' in ui, "Кнопка «Удалить» есть на вкладке «Счета»")
    check("def _delete" in ui, "У неё есть обработчик")
    check("askyesno" in ui.split("def _delete", 1)[1][:400],
          "Перед удалением спрашивает подтверждение")
    check("supervisor.stop" in ui.split("def _delete", 1)[1][:500],
          "Счёт сначала останавливается, потом удаляется")

    # Удаление должно доходить до диска
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "accounts.json"
        store = AccountStore(path)
        store.add(make(1, password="раз"), PASSWORD, SALT)
        store.add(make(2, password="два"), PASSWORD, SALT)
        check(store.remove(1, PASSWORD, SALT), "Счёт удалён")
        again = AccountStore(path).load(PASSWORD, SALT)
        check([a.login for a in again] == [2], "На диске остался только второй",
              str([a.login for a in again]))
        check(again[0].password == "два", "И его пароль не пострадал")


def test_no_daily_loss_stop() -> None:
    """Владелец: «достижения убытка в день убери».

    Дневной порог был ПОСЛЕДНЕЙ работающей остановкой: общий
    USE_DAILY_LOSS_LIMIT давно выключен, но у каждого счёта в accounts.json
    свой daily_loss_percent, и глобальную галочку он не читает — поймав
    −3% за день, счёт закрывал позиции и молчал до завтра."""
    print("\n=== 3ж. Дневной порог убытка убран ===")

    check(Account().daily_loss_percent == 0,
          "У нового счёта дневного порога нет",
          str(Account().daily_loss_percent))

    ui = (APP / "accounts_tab.py").read_text(encoding="utf-8")
    check("or 3.0" not in ui,
          "Пустое поле больше не превращается в 3% (было `or 3.0`)")

    sup_src = (APP / "account_supervisor.py").read_text(encoding="utf-8")
    check('daily_loss_percent", 3.0' not in sup_src,
          "Старый счёт без этого поля тоже не получает порог из воздуха")

    # Поведение: с нулём остановки нет, с положительным числом — есть
    # (настройку не удалили, она просто выключена по умолчанию)
    import account_supervisor as sup_mod

    class _Q:
        def put_nowait(self, item):
            pass

    class _FakeMT5:
        def positions_get(self):
            return []

    runner = sup_mod.AccountRunner([{"login": 777, "name": "тест"}], _Q(), _Q())
    saved_mt5 = sup_mod.mt5
    sup_mod.mt5 = _FakeMT5()
    try:
        state = runner.states[777]
        state.day_start_equity = 1000.0
        state.daily_pct = -25.0
        runner.check_daily_limit(777, {"daily_loss_percent": 0.0})
        check(not state.trading_blocked,
              "Убыток −25% за день торговлю не останавливает")
        runner.check_daily_limit(777, {})          # поля вообще нет
        check(not state.trading_blocked,
              "Счёт без поля daily_loss_percent тоже не останавливается")
        runner.check_daily_limit(777, {"daily_loss_percent": 3.0})
        check(state.trading_blocked,
              "Если человек сам впишет порог — он по-прежнему работает")
    finally:
        sup_mod.mt5 = saved_mt5


def test_daily_loss_migration_for_saved_accounts() -> None:
    """Порог лежит в accounts.json — файле, который обновление не трогает.
    Значит у УЖЕ добавленных счетов его надо снять отдельно, один раз."""
    print("\n=== 3з. Старым счетам порог снимается один раз ===")
    sys.path.insert(0, str(APP))
    import config_migrate as cm

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "accounts.json"
        store = AccountStore(path)
        first = make(1, password="секрет-раз")
        first.daily_loss_percent = 3.0
        store.add(first, PASSWORD, SALT)

        note = cm.clear_account_daily_loss(str(path))
        check(bool(note), "Миграция сработала и объяснила, что сделала", note)

        after = AccountStore(path)
        loaded = after.load(PASSWORD, SALT)
        check(loaded[0].daily_loss_percent == 0, "Порог снят",
              str(loaded[0].daily_loss_percent))
        check(loaded[0].password == "секрет-раз",
              "Пароль счёта при этом цел", loaded[0].password)

        # Второй раз — уже ничего не делает
        check(cm.clear_account_daily_loss(str(path)) == "",
              "Повторный запуск ничего не меняет")

        # И если человек ОСОЗНАННО впишет порог заново, миграция его не тронет
        loaded[0].daily_loss_percent = 5.0
        after.save(PASSWORD, SALT)
        cm.clear_account_daily_loss(str(path))
        again = AccountStore(path).load(PASSWORD, SALT)
        check(again[0].daily_loss_percent == 5.0,
              "Вписанный вручную порог переживает перезапуск",
              str(again[0].daily_loss_percent))
        check(again[0].password == "секрет-раз",
              "Пароль по-прежнему цел")

    # Нет файла счетов — не падать
    with tempfile.TemporaryDirectory() as tmp:
        check(cm.clear_account_daily_loss(str(Path(tmp) / "accounts.json")) == "",
              "Без файла счетов миграция молчит")

    start = (APP / "desktop_app.py").read_text(encoding="utf-8")
    check("config_migrate.clear_account_daily_loss()" in start,
          "Миграция вызывается при запуске программы")


def test_accounts_saved_without_pressing_buttons() -> None:
    """Владелец: «пытался сохранить счёт, пусть сразу сохраняет».

    Он нажал «☁ Сохранить счета в облако» и увидел «Не указан токен GitHub» —
    и понял это как «счёт не сохранился». На самом деле счёт к тому моменту
    уже лежал на диске, а облако — только запасная копия. Здесь проверяем
    обе стороны: список уезжает в облако сам, а без облака ничего не
    ломается и текст об этом честный."""
    print("\n=== 3и. Счета сохраняются сами ===")
    ui = (APP / "accounts_tab.py").read_text(encoding="utf-8")

    check("def _autobackup" in ui, "Автосохранение в облако есть")
    for place in ("self.store.add(dialog.result, password, salt)",
                  "self.store.replace(account.login, dialog.result, password, salt)",
                  "self.store.remove(account.login, password, salt)"):
        tail = ui.split(place, 1)[1][:200]
        check("_autobackup()" in tail,
              f"После «{place.split('.')[1].split('(')[0]}» список уходит в облако сам")
    saver = ui.split("def _save_accounts", 1)[1][:300]
    check("_autobackup()" in saver,
          "Галочка «включён/выключен» тоже сохраняется в облако сама")

    # Без облака автосохранение молчит: ругаться окном на каждый щелчок нельзя
    body = ui.split("def _autobackup", 1)[1].split("\n    def ", 1)[0]
    check("ready()" in body, "Сначала проверяется, настроено ли облако")
    check("return" in body.split("ready()", 1)[1][:200],
          "Не настроено — просто выходим, без окна с ошибкой")
    check("messagebox" not in body, "Автосохранение не показывает окон вовсе")
    check("Thread" in body, "Отправка в фоне — окно не подвисает")

    after = ui.split("def _after_autobackup", 1)[1].split("\n    def ", 1)[0]
    check("messagebox" not in after, "И об итоге сообщает строкой, а не окном")

    # Текст про ненастроенное облако обязан начинаться с главного
    warn = ui.split('"Облако не настроено",', 1)[1][:600]
    check("уже сохранены" in warn,
          "Первым делом сказано, что счета уже сохранены на компьютере")


def test_locked_password_survives_other_saves() -> None:
    """Реальная жалоба владельца: "не должен удалять счета при перезапуске".

    Причина: по умолчанию REQUIRE_LOGIN=False и без "запомненного" пароля
    программа открывается БЕЗ пароля входа — сессионный пароль пустой.
    accounts.json при этом читается с ПУСТЫМ паролем: расшифровать чужим
    ключом настоящий пароль счёта нельзя, он временно недоступен (это
    ожидаемо). Раньше save() в таком состоянии брал пустую строку из памяти
    и НАВСЕГДА шифровал её поверх настоящего пароля — одно нажатие ЛЮБОЙ
    кнопки на вкладке "Счета" (переключить галочку у ДРУГОГО счёта, добавить
    третий, удалить четвёртый — save() перезаписывает файл ЦЕЛИКОМ) стирало
    пароль безвозвратно. Здесь именно это и проверяется на каждом таком
    действии."""
    print("\n=== 3б. Заблокированный (не расшифрованный) пароль не стирается ===")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "accounts.json"
        store = AccountStore(path)
        store.add(make(1001, password="реальный-пароль-1"), PASSWORD, SALT)
        store.add(make(1002, password="реальный-пароль-2"), PASSWORD, SALT)

        # "Перезапуск программы" без пароля входа — типичный REQUIRE_LOGIN=False
        restarted = AccountStore(path)
        restarted.load("", SALT)
        check(restarted.accounts[0].password == "" and restarted.accounts[1].password == "",
              "с пустым паролем входа пароли счетов недоступны (ожидаемо)")
        check(restarted.accounts[0].password_locked() is True,
              "счёт помечен как заблокированный, а не как «без пароля»")
        check("не расшифрован" in restarted.accounts[0].validate()[0],
              "сообщение объясняет причину, а не «не указан пароль»",
              restarted.accounts[0].validate())

        # Дальше пользователь делает РАЗНЫЕ действия тем же (пустым) паролем —
        # ни одно из них не должно повредить чужой зашифрованный пароль.
        restarted.accounts[0].enabled = False           # переключил галочку
        restarted.save("", SALT)

        restarted.add(make(1003, password="секрет-три"), "", SALT)  # добавил счёт

        check(restarted.remove(1003, "", SALT), "удаление счёта без пароля работает")

        # Теперь настоящий вход — все пароли обязаны быть целы
        reloaded = AccountStore(path).load(PASSWORD, SALT)
        by_login = {a.login: a for a in reloaded}
        check(by_login[1001].password == "реальный-пароль-1",
              "первый пароль пережил чужие сохранения", by_login[1001].password)
        check(by_login[1002].password == "реальный-пароль-2",
              "второй пароль пережил чужие сохранения", by_login[1002].password)
        check(by_login[1001].enabled is False, "при этом сама галочка сохранилась")
        check(1003 not in by_login, "добавленный и удалённый счёт по-прежнему удалён")


def test_locked_account_not_confused_with_empty() -> None:
    print("\n=== 3в. Заблокированный счёт отличим от счёта без пароля ===")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "accounts.json"
        store = AccountStore(path)
        store.add(make(1, password="настоящий"), PASSWORD, SALT)   # с паролем
        store.add(make(2, password=""), PASSWORD, SALT)            # без пароля

        locked_view = AccountStore(path).load("не тот пароль", SALT)
        by_login = {a.login: a for a in locked_view}
        check(by_login[1].password_locked() is True,
              "счёт с реальным (но нерасшифрованным) паролем — заблокирован")
        check(by_login[2].password_locked() is False,
              "счёт, у которого пароля никогда не было — просто не заполнен")


def test_correcting_password_still_works() -> None:
    """Обычная, ЖЕЛАЕМАЯ смена пароля должна по-прежнему сохраняться."""
    print("\n=== 3г. Осознанная смена пароля работает как раньше ===")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "accounts.json"
        store = AccountStore(path)
        store.add(make(1, password="старый"), PASSWORD, SALT)

        again = AccountStore(path)
        again.load(PASSWORD, SALT)
        again.accounts[0].password = "новый"
        again.save(PASSWORD, SALT)

        check(AccountStore(path).load(PASSWORD, SALT)[0].password == "новый",
              "новый пароль записан")


def test_migration() -> None:
    print("\n=== 5. Перенос старого счёта из config.py ===")

    class FakeConfig:
        MT5_LOGIN = 555001
        MT5_PASSWORD = "старый-пароль"
        MT5_SERVER = "Old-Broker"
        MT5_TERMINAL_PATH = ""
        SYMBOLS = ["EURUSDs", "XAUUSDs"]
        MAGIC_NUMBER = 234567

    with tempfile.TemporaryDirectory() as tmp:
        store = AccountStore(Path(tmp) / "a.json")
        check(migrate_from_config(FakeConfig, store, PASSWORD, SALT),
              "одиночный счёт перенесён в список")
        check(len(store.accounts) == 1, "в списке один счёт")
        moved = store.accounts[0]
        check(moved.login == 555001 and moved.password == "старый-пароль",
              "логин и пароль перенесены")
        check(moved.symbols == ["EURUSDs", "XAUUSDs"], "инструменты перенесены")
        check(moved.magic == 234567, "MagicNumber перенесён")

        check(not migrate_from_config(FakeConfig, store, PASSWORD, SALT),
              "повторный перенос не дублирует счёт")

        class NoAccount:
            MT5_LOGIN = 0

        empty = AccountStore(Path(tmp) / "b.json")
        check(not migrate_from_config(NoAccount, empty, PASSWORD, SALT),
              "если счёта в config не было — переносить нечего")


class FakeProcess:
    def __init__(self):
        self.alive = True
        self.terminated = False

    def is_alive(self):
        return self.alive

    def join(self, _t=None):
        self.alive = False

    def terminate(self):
        self.terminated = True
        self.alive = False


def test_grouping() -> None:
    print("\n=== 6. Группировка: параллельно или по очереди ===")
    spawned = []

    def fake_spawn(accounts_list, state_q, cmd_q):
        spawned.append([a["login"] for a in accounts_list])
        return FakeProcess()

    sup = AccountSupervisor(spawn_fn=fake_spawn)
    started, messages = sup.start([
        make(1, terminal_path="C:/mt5-a/terminal64.exe"),  # свой терминал
        make(2, terminal_path="C:/mt5-b/terminal64.exe"),  # свой терминал
        make(3),                                            # общий терминал
        make(4),                                            # общий терминал
    ])

    check(started == 4, "запущены все четыре счёта", str(started))
    own = [g for g in spawned if len(g) == 1]
    shared = [g for g in spawned if len(g) > 1]
    check(len(own) == 2, "два счёта со своими терминалами — отдельные процессы",
          str(spawned))
    check(len(shared) == 1 and sorted(shared[0]) == [3, 4],
          "счета без своего терминала объединены в один процесс", str(spawned))
    check(any("по очереди" in m for m in messages),
          "программа предупреждает про опрос по очереди", str(messages))

    print("\n=== 7. Отказы при запуске ===")
    sup2 = AccountSupervisor(spawn_fn=fake_spawn)
    started, messages = sup2.start([make(9, password="")])
    check(started == 0, "ненастроенный счёт не запускается")
    check(any("не указан пароль" in m for m in messages),
          "причина отказа понятна", str(messages))

    sup2.start([make(10)])
    started, messages = sup2.start([make(10)])
    check(started == 0 and any("уже запущен" in m for m in messages),
          "повторный запуск того же счёта отклоняется", str(messages))


def test_commands_and_totals() -> None:
    print("\n=== 8. Команды и сводка ===")
    queues = []

    def fake_spawn(accounts_list, state_q, cmd_q):
        queues.append(cmd_q)
        return FakeProcess()

    sup = AccountSupervisor(spawn_fn=fake_spawn)
    sup.start([make(1, terminal_path="C:/a/terminal64.exe"),
               make(2, terminal_path="C:/b/terminal64.exe")])

    check(sup.close_all(1), "команда «закрыть всё» отправлена")
    check(sup.close_profitable(1), "команда «закрыть прибыльные» отправлена")
    check(sup.close_losing(2), "команда «закрыть убыточные» отправлена")
    check(sup.close_ticket(1, 777), "команда закрытия позиции отправлена")
    check(not sup.close_all(999), "команда для незапущенного счёта не уходит")
    check(sup.close_all_everywhere() == 2, "аварийная кнопка шлёт на все счета")

    # Команда обязана содержать номер счёта — иначе процесс с несколькими
    # счетами не поймёт, к какому она относится
    cmds = []
    while not queues[0].empty():
        cmds.append(queues[0].get_nowait())
    check(all("login" in c for c in cmds if c["kind"] != "stop"),
          "в каждой команде указан номер счёта", str(cmds[:2]))
    check(all(c["login"] == 1 for c in cmds if c["kind"] != "stop"),
          "команды ушли на нужный счёт")

    now = time.time()
    for login, profit, equity in ((1, 25.0, 1000.0), (2, -5.0, 2000.0)):
        st = AccountState(login=login, connected=True, status="подключён",
                          equity=equity, balance=equity, profit=profit,
                          positions=[{"profit": profit}])
        st.updated_at = now
        sup._states[login] = st

    totals = sup.totals()
    check(abs(totals["profit"] - 20.0) < 1e-9, "общий результат складывается",
          str(totals["profit"]))
    check(abs(totals["equity"] - 3000.0) < 1e-9, "средства складываются")
    check(totals["positions"] == 2, "позиции считаются по всем счетам")
    check(totals["connected"] == 2, "подключённые счета считаются")

    print("\n=== 9. Зависший процесс и остановка ===")
    check(not sup.is_stale(1, now=now), "свежее состояние не считается зависшим")
    check(sup.is_stale(1, now=now + 30), "молчание 30 секунд = завис")

    sup.stop(1)
    check(not sup.is_running(1), "счёт остановлен")
    check(sup.is_running(2), "второй счёт продолжает работать")
    check(not sup.is_stale(1, now=now + 30), "остановленный счёт зависшим не считается")

    sup.stop_all()
    check(sup.totals()["running"] == 0, "остановлены все счета")




def test_symbol_resolution() -> None:
    print("\n=== 10. Пары подтягиваются от брокера ===")
    # Одна и та же пара у разных брокеров называется по-разному
    switch = ["EURUSDs", "XAUUSDs", "GBPUSDs", "BTCUSDs"]
    exness = ["EURUSD", "XAUUSD", "GBPUSD", "EURUSDm"]
    icm = ["EURUSD.a", "XAUUSD.a", "GBPUSD.a"]

    check(resolve_symbol("EURUSD", switch) == "EURUSDs",
          "суффикс s: EURUSD -> EURUSDs", str(resolve_symbol("EURUSD", switch)))
    check(resolve_symbol("EURUSD", icm) == "EURUSD.a",
          "суффикс .a: EURUSD -> EURUSD.a", str(resolve_symbol("EURUSD", icm)))
    check(resolve_symbol("EURUSD", exness) == "EURUSD",
          "точное имя выигрывает у похожего (EURUSD, а не EURUSDm)",
          str(resolve_symbol("EURUSD", exness)))
    check(resolve_symbol("XAUUSDs", switch) == "XAUUSDs",
          "имя уже правильное — возвращается как есть")

    check(resolve_symbol("eurusds", switch) == "EURUSDs",
          "регистр не важен", str(resolve_symbol("eurusds", switch)))
    check(resolve_symbol("EUR/USD", exness) == "EURUSD",
          "разделители игнорируются", str(resolve_symbol("EUR/USD", exness)))

    check(resolve_symbol("НЕТТАКОЙ", switch) is None, "несуществующая пара -> None")
    check(resolve_symbol("", switch) is None, "пустое имя -> None")
    check(resolve_symbol("EURUSD", []) is None, "пустой список брокера -> None")

    # Из нескольких подходящих берём самое короткое: это базовая пара,
    # а не производная вроде EURUSDs.raw
    many = ["EURUSDs.raw", "EURUSDs", "EURUSDs.pro"]
    check(resolve_symbol("EURUSD", many) == "EURUSDs",
          "из нескольких вариантов берётся базовая пара",
          str(resolve_symbol("EURUSD", many)))

    print("\n=== 11. Сопоставление списка пар ===")
    mapping, missing = resolve_symbols(["EURUSD", "XAUUSD", "ЧЕГОНЕТ"], switch)
    check(mapping == {"EURUSD": "EURUSDs", "XAUUSD": "XAUUSDs"},
          "найденные пары сопоставлены", str(mapping))
    check(missing == ["ЧЕГОНЕТ"], "ненайденные возвращаются отдельно", str(missing))

    mapping, missing = resolve_symbols([], switch)
    check(mapping == {} and missing == [], "пустой запрос -> пустой ответ")

    # Один и тот же список на трёх брокерах даёт три разных результата
    wanted = ["EURUSD", "XAUUSD"]
    results = [tuple(resolve_symbols(wanted, b)[0].values()) for b in (switch, exness, icm)]
    check(len(set(results)) == 3,
          "один список пар на трёх брокерах даёт три разных набора имён",
          str(results))


def test_symbols_in_state() -> None:
    print("\n=== 12. Список пар доходит до интерфейса ===")
    state = AccountState(login=1)
    check(state.available_symbols == [],
          "у нового счёта список пар пуст (счёт ещё не запускался)")
    state.available_symbols = ["EURUSDs", "XAUUSDs"]
    check(len(state.available_symbols) == 2, "список пар хранится в состоянии счёта")


# =====================================================================
# 13. Резервная копия счетов в облаке
# =====================================================================
class _FakeCloud:
    """Заглушка cloud_journal: запоминает, что в неё положили, ничего не шлёт
    по сети."""

    def __init__(self):
        self.files = {}          # путь -> текст
        self.put_calls = []
        self.ready_result = (True, "")

    def ready(self):
        return self.ready_result

    def put_file(self, path, text, message):
        self.put_calls.append((path, text, message))
        self.files[path] = text
        return "abc123def456"

    def get_file(self, path):
        return self.files.get(path)

    def remote_sha(self, path):
        return "sha" if path in self.files else None

    def explain_error(self, exc):
        return f"ошибка: {exc}"


def test_accounts_backup_not_ready_by_default() -> None:
    print("\n=== 13. Резервная копия счетов в облаке ===")
    check(ab.BACKUP_PATH != cj.folder(),
          "путь резервной копии не совпадает с папкой журнала сделок",
          ab.BACKUP_PATH)

    old_enabled = _cfg.JOURNAL_CLOUD_ENABLED
    _cfg.JOURNAL_CLOUD_ENABLED = False
    ok, reason = ab.ready()
    check(ok is False and reason, "Без настроенного облака — понятная причина", reason)
    _cfg.JOURNAL_CLOUD_ENABLED = old_enabled


def test_accounts_backup_upload_uses_real_file_asis() -> None:
    """Файл уходит В ОБЛАКО КАК ЕСТЬ — со всеми "enc:..." полями. Модуль не
    расшифровывает и не пересобирает содержимое: значит секреты счетов
    уходят наружу ровно в том виде, в каком уже лежат зашифрованными на
    диске, и ничего лишнего не подмешивается."""
    with tempfile.TemporaryDirectory() as d:
        store = AccountStore(Path(d) / "accounts.json")
        store.add(make(1, password="настоящий-пароль-брокера"), PASSWORD, SALT)
        raw_on_disk = (Path(d) / "accounts.json").read_text(encoding="utf-8")
        check("настоящий-пароль-брокера" not in raw_on_disk,
              "на диске пароль уже зашифрован (проверка честности теста)")

        fake = _FakeCloud()
        saved_app_dir, saved_put, saved_get, saved_ready, saved_sha, saved_err = (
            ab.app_dir, cj.put_file, cj.get_file, cj.ready, cj.remote_sha, cj.explain_error)
        ab.app_dir = lambda: d
        cj.put_file, cj.get_file = fake.put_file, fake.get_file
        cj.ready, cj.remote_sha, cj.explain_error = fake.ready, fake.remote_sha, fake.explain_error
        try:
            result = ab.upload()
            check(result["ok"] is True, "Загрузка прошла", str(result))
            check(len(fake.put_calls) == 1, "Ровно один файл отправлен")
            sent_path, sent_text, _msg = fake.put_calls[0]
            check(sent_path == ab.BACKUP_PATH, "Отправлен по правильному пути")
            check(sent_text == raw_on_disk,
                  "Содержимое совпадает байт в байт с файлом на диске")
            check("настоящий-пароль-брокера" not in sent_text,
                  "В облако не ушёл открытый пароль")
        finally:
            ab.app_dir, cj.put_file, cj.get_file = saved_app_dir, saved_put, saved_get
            cj.ready, cj.remote_sha, cj.explain_error = saved_ready, saved_sha, saved_err


def test_accounts_backup_upload_without_local_file() -> None:
    with tempfile.TemporaryDirectory() as d:
        fake = _FakeCloud()
        saved_app_dir, saved_ready = ab.app_dir, cj.ready
        ab.app_dir = lambda: d
        cj.ready = fake.ready
        try:
            result = ab.upload()
            check(result["ok"] is False and "не найден" in result["error"].lower(),
                  "Нет локального файла — понятная ошибка, а не падение",
                  str(result))
        finally:
            ab.app_dir, cj.ready = saved_app_dir, saved_ready


def test_accounts_backup_restore_preserves_local_file() -> None:
    """Восстановление никогда не стирает молча: текущий файл (если есть)
    сохраняется рядом с припиской .before-restore ДО замены."""
    with tempfile.TemporaryDirectory() as d:
        local_path = Path(d) / "accounts.json"
        local_path.write_text('{"version": 1, "accounts": ["локальная копия"]}',
                              encoding="utf-8")

        fake = _FakeCloud()
        fake.files[ab.BACKUP_PATH] = '{"version": 1, "accounts": ["облачная копия"]}'
        saved_app_dir, saved_get, saved_ready, saved_sha, saved_err = (
            ab.app_dir, cj.get_file, cj.ready, cj.remote_sha, cj.explain_error)
        ab.app_dir = lambda: d
        cj.get_file, cj.ready = fake.get_file, fake.ready
        cj.remote_sha, cj.explain_error = fake.remote_sha, fake.explain_error
        try:
            result = ab.restore()
            check(result["ok"] is True, "Восстановление прошло", str(result))
            check(local_path.read_text(encoding="utf-8") ==
                  '{"version": 1, "accounts": ["облачная копия"]}',
                  "На диске теперь облачная копия")
            backup_path = Path(str(local_path) + ".before-restore")
            check(backup_path.exists(), "Старый файл сохранён рядом")
            check("локальная копия" in backup_path.read_text(encoding="utf-8"),
                  "И в нём именно то, что было ДО восстановления")
        finally:
            ab.app_dir, cj.get_file, cj.ready = saved_app_dir, saved_get, saved_ready
            cj.remote_sha, cj.explain_error = saved_sha, saved_err


def test_accounts_backup_restore_nothing_in_cloud() -> None:
    with tempfile.TemporaryDirectory() as d:
        fake = _FakeCloud()  # облако пустое
        saved_app_dir, saved_get, saved_ready = ab.app_dir, cj.get_file, cj.ready
        ab.app_dir = lambda: d
        cj.get_file, cj.ready = fake.get_file, fake.ready
        try:
            result = ab.restore()
            check(result["ok"] is False and "нет" in result["error"].lower(),
                  "Пустое облако — понятная причина, ничего не портится",
                  str(result))
        finally:
            ab.app_dir, cj.get_file, cj.ready = saved_app_dir, saved_get, saved_ready


def test_accounts_backup_roundtrip() -> None:
    """Полный круг: сохранить -> испортить/потерять локально -> восстановить
    -> те же счета с теми же (зашифрованными) паролями на диске."""
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        store = AccountStore(Path(d1) / "accounts.json")
        store.add(make(1001, password="пароль-раз"), PASSWORD, SALT)
        store.add(make(1002, password="пароль-два"), PASSWORD, SALT)
        original_raw = (Path(d1) / "accounts.json").read_text(encoding="utf-8")

        fake = _FakeCloud()
        saved_app_dir, saved_put, saved_get, saved_ready = (
            ab.app_dir, cj.put_file, cj.get_file, cj.ready)
        cj.put_file, cj.get_file, cj.ready = fake.put_file, fake.get_file, fake.ready
        try:
            ab.app_dir = lambda: d1
            check(ab.upload()["ok"] is True, "Сохранено с первого компьютера")

            # "Переустановка" — совсем другая (пустая) папка
            ab.app_dir = lambda: d2
            result = ab.restore()
            check(result["ok"] is True, "Восстановлено на втором компьютере", str(result))

            restored = AccountStore(Path(d2) / "accounts.json")
            loaded = restored.load(PASSWORD, SALT)
            check(len(loaded) == 2, "Оба счёта на месте после переустановки")
            by_login = {a.login: a for a in loaded}
            check(by_login[1001].password == "пароль-раз", "Первый пароль восстановлен")
            check(by_login[1002].password == "пароль-два", "Второй пароль восстановлен")
            check((Path(d2) / "accounts.json").read_text(encoding="utf-8") == original_raw,
                  "Восстановленный файл побайтово совпадает с оригиналом")
        finally:
            ab.app_dir, cj.put_file, cj.get_file, cj.ready = (
                saved_app_dir, saved_put, saved_get, saved_ready)


def main_run() -> int:
    test_validation()
    test_parallel_flag()
    test_store_encryption()
    test_accounts_file_survives_restart_in_exe()
    test_delete_button_exists()
    test_no_daily_loss_stop()
    test_daily_loss_migration_for_saved_accounts()
    test_accounts_saved_without_pressing_buttons()
    test_locked_password_survives_other_saves()
    test_locked_account_not_confused_with_empty()
    test_correcting_password_still_works()
    test_store_operations()
    test_migration()
    test_grouping()
    test_commands_and_totals()
    test_symbol_resolution()
    test_symbols_in_state()
    test_accounts_backup_not_ready_by_default()
    test_accounts_backup_upload_uses_real_file_asis()
    test_accounts_backup_upload_without_local_file()
    test_accounts_backup_restore_preserves_local_file()
    test_accounts_backup_restore_nothing_in_cloud()
    test_accounts_backup_roundtrip()

    print("\n===========================================")
    print(f"Пройдено: {passed}, провалено: {failed}")
    print("===========================================")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main_run())
