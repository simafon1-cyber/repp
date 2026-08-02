#!/usr/bin/env python3
"""Тесты программы Trader: шифрование паролей, хранение счетов, супервизор.

Запуск:  python3 tests/test_trader_app.py
MetaTrader 5 не нужен — процессы счетов подменяются заглушками.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent / "trader_app"))

from core import secrets  # noqa: E402
from core.accounts import MIN_POLL_MS, Account, AccountStore  # noqa: E402
from core.mt5_worker import AccountState  # noqa: E402
from core.supervisor import Supervisor  # noqa: E402

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


def make_account(login=100001, **kw) -> Account:
    data = dict(name="Тест", login=login, server="Broker-Demo",
                password="секрет123", symbols=["EURUSD"])
    data.update(kw)
    return Account(**data)


def test_secrets() -> None:
    print("\n=== 1. Шифрование паролей ===")
    original = "мой пароль 123 !@#"
    stored = secrets.encrypt(original)

    check(stored != original, "зашифрованная строка не совпадает с исходной")
    check(original not in stored, "пароль не виден в зашифрованной строке")
    check(secrets.decrypt(stored) == original, "расшифровка возвращает исходный пароль")
    check(secrets.encrypt("") == "", "пустой пароль не шифруется")
    check(secrets.decrypt("") == "", "пустая строка расшифровывается в пустую")

    # Старый формат (пароль лежал открытым текстом) должен читаться
    check(secrets.decrypt("старый_пароль") == "старый_пароль",
          "файл старого формата читается без ошибки")

    # На Windows строка обязана быть помечена как защищённая DPAPI
    if secrets.IS_WINDOWS:
        check(secrets.is_protected(stored), "на Windows используется шифрование DPAPI")
    else:
        check(not secrets.is_protected(stored),
              "вне Windows строка НЕ помечена как защищённая (честно)")
        check("ВНИМАНИЕ" in secrets.storage_status(),
              "вне Windows интерфейс предупреждает об отсутствии защиты")

    # Юникод и длинные пароли
    for sample in ["пароль", "a" * 500, "🔐ключ", "with spaces and\ttabs"]:
        check(secrets.decrypt(secrets.encrypt(sample)) == sample,
              f"пароль вида {sample[:12]!r} шифруется и читается обратно")


def test_account_validation() -> None:
    print("\n=== 2. Проверка данных счёта ===")
    check(make_account().validate() == [], "корректный счёт проходит проверку")
    check(make_account(login=0).validate() != [], "без номера счёта — ошибка")
    check(make_account(password="").validate() != [], "без пароля — ошибка")
    check(make_account(server="").validate() != [], "без сервера — ошибка")
    check(make_account(symbols=[]).validate() != [], "без инструментов — ошибка")
    check(make_account(risk_percent=0).validate() != [], "нулевой риск — ошибка")
    check(make_account(risk_percent=50).validate() != [], "риск 50% — ошибка")
    check(make_account(max_positions=0).validate() != [], "ноль позиций — ошибка")
    check(make_account(poll_interval_ms=1).validate() != [],
          f"интервал опроса меньше {MIN_POLL_MS} мс — ошибка")
    check(make_account(poll_interval_ms=MIN_POLL_MS).validate() == [],
          f"интервал ровно {MIN_POLL_MS} мс — допустим")


def test_store() -> None:
    print("\n=== 3. Хранение счетов на диске ===")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "accounts.json"
        store = AccountStore(path)

        check(store.load() == [], "нет файла — пустой список, без ошибки")

        store.add(make_account(100001, name="Первый"))
        store.add(make_account(100002, name="Второй", password="другой пароль"))
        check(len(store.accounts) == 2, "два счёта добавлены")

        # Пароли не должны лежать в файле открытым текстом
        raw = path.read_text(encoding="utf-8")
        check("секрет123" not in raw, "пароль не виден в файле accounts.json")
        check("другой пароль" not in raw, "второй пароль тоже не виден")
        check("password_encrypted" in raw, "пароль сохранён в зашифрованном поле")
        data = json.loads(raw)
        check("password" not in data["accounts"][0],
              "открытого поля password в файле нет")

        # Перечитывание возвращает те же данные
        again = AccountStore(path)
        loaded = again.load()
        check(len(loaded) == 2, "оба счёта прочитаны обратно")
        check(loaded[0].password == "секрет123", "пароль расшифрован верно")
        check(loaded[1].password == "другой пароль", "второй пароль расшифрован верно")
        check(loaded[0].name == "Первый" and loaded[1].name == "Второй",
              "названия сохранились")
        check(loaded[0].symbols == ["EURUSD"], "список инструментов сохранился")

        check(again.find(100002) is not None, "поиск по номеру счёта работает")
        check(again.find(999999) is None, "несуществующий счёт не находится")

        check(again.remove(100001), "счёт удаляется")
        check(len(AccountStore(path).load()) == 1, "удаление сохранено на диск")
        check(not again.remove(100001), "повторное удаление возвращает False")

        # Битый файл не должен ронять программу
        path.write_text("{это не json", encoding="utf-8")
        check(AccountStore(path).load() == [], "испорченный файл не роняет программу")


def test_enabled_accounts() -> None:
    print("\n=== 4. Отбор счетов для запуска ===")
    with tempfile.TemporaryDirectory() as tmp:
        store = AccountStore(Path(tmp) / "a.json")
        store.accounts = [
            make_account(1, name="рабочий"),
            make_account(2, name="выключен", enabled=False),
            make_account(3, name="без пароля", password=""),
        ]
        ready = store.enabled_accounts()
        logins = [a.login for a in ready]
        check(logins == [1], "к запуску берётся только настроенный и включённый счёт",
              str(logins))


class FakeProcess:
    """Заглушка процесса счёта — настоящий MT5 в тестах не нужен."""

    def __init__(self, *_args, **_kw):
        self.alive = True
        self.terminated = False
        self.joined = False

    def is_alive(self):
        return self.alive

    def join(self, _timeout=None):
        self.joined = True
        self.alive = False

    def terminate(self):
        self.terminated = True
        self.alive = False


def test_supervisor() -> None:
    print("\n=== 5. Управление процессами счетов ===")
    created = []

    def fake_start(account_dict, state_q, cmd_q):
        created.append((account_dict, cmd_q))
        return FakeProcess()

    sup = Supervisor(start_fn=fake_start)
    acc1 = make_account(1)
    acc2 = make_account(2)

    ok, msg = sup.start(acc1)
    check(ok, "счёт запускается", msg)
    check(sup.is_running(1), "счёт числится запущенным")

    ok, msg = sup.start(acc1)
    check(not ok and "уже запущен" in msg, "повторный запуск того же счёта отклоняется", msg)

    ok, msg = sup.start(make_account(3, password=""))
    check(not ok and "не настроен" in msg, "ненастроенный счёт не запускается", msg)

    sup.start(acc2)
    check(sup.totals()["running"] == 2, "запущено два счёта")

    # Пароль должен уходить в процесс — иначе вход в счёт невозможен
    check(created[0][0]["password"] == "секрет123", "процесс получает пароль счёта")
    check(created[0][0]["login"] == 1, "процесс получает номер счёта")

    print("\n=== 6. Команды доходят до процессов ===")
    check(sup.close_all(1), "команда «закрыть всё» отправлена")
    check(sup.close_profitable(1), "команда «закрыть прибыльные» отправлена")
    check(sup.close_losing(1), "команда «закрыть убыточные» отправлена")
    check(sup.close_ticket(1, 12345), "команда закрытия одной позиции отправлена")
    check(not sup.close_all(999), "команда для незапущенного счёта не отправляется")

    sent = sup.close_all_everywhere()
    check(sent == 2, "аварийная кнопка шлёт команду на все счета", str(sent))

    # Проверяем, что команды реально легли в очередь процесса
    cmd_q = created[0][1]
    kinds = []
    while not cmd_q.empty():
        kinds.append(cmd_q.get_nowait()["kind"])
    check("close_all" in kinds and "close_one" in kinds,
          "очередь процесса содержит отправленные команды", str(kinds))

    print("\n=== 7. Остановка ===")
    sup.stop(1)
    check(not sup.is_running(1), "счёт остановлен")
    check(sup.state(1).status == "остановлен", "статус обновлён")
    sup.stop_all()
    check(sup.totals()["running"] == 0, "остановлены все счета")


def test_totals_and_stale() -> None:
    print("\n=== 8. Сводка и определение зависшего процесса ===")

    def fake_start(*_a, **_k):
        return FakeProcess()

    sup = Supervisor(start_fn=fake_start)
    sup.start(make_account(1))
    sup.start(make_account(2))

    import time

    now = time.time()
    for login, profit, equity in ((1, 10.5, 1000.0), (2, -4.5, 2000.0)):
        state = AccountState(login=login, connected=True, status="подключён",
                             equity=equity, balance=equity, profit=profit,
                             positions=[{"profit": profit}])
        state.updated_at = now
        sup._states[login] = state

    totals = sup.totals()
    check(abs(totals["profit"] - 6.0) < 1e-9, "общий результат складывается по счетам",
          str(totals["profit"]))
    check(abs(totals["equity"] - 3000.0) < 1e-9, "средства складываются")
    check(totals["positions"] == 2, "позиции считаются по всем счетам")
    check(totals["connected"] == 2, "подключённые счета считаются")

    check(not sup.is_stale(1, now=now), "свежее состояние не считается зависшим")
    check(sup.is_stale(1, now=now + 30), "молчание 30 секунд = процесс завис")

    sup.stop(1)
    check(not sup.is_stale(1, now=now + 30), "остановленный счёт зависшим не считается")


def main_run() -> int:
    test_secrets()
    test_account_validation()
    test_store()
    test_enabled_accounts()
    test_supervisor()
    test_totals_and_stale()

    print("\n===========================================")
    print(f"Пройдено: {passed}, провалено: {failed}")
    print("===========================================")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main_run())
