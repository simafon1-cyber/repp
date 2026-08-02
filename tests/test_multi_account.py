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
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent))

import accounts as acc_mod  # noqa: E402
from account_supervisor import AccountState, AccountSupervisor  # noqa: E402
from accounts import MIN_POLL_MS, Account, AccountStore, migrate_from_config  # noqa: E402

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


def main_run() -> int:
    test_validation()
    test_parallel_flag()
    test_store_encryption()
    test_store_operations()
    test_migration()
    test_grouping()
    test_commands_and_totals()

    print("\n===========================================")
    print(f"Пройдено: {passed}, провалено: {failed}")
    print("===========================================")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main_run())
