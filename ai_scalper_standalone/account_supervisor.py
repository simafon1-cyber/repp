"""Работа с несколькими счетами MT5 одновременно.

Ограничение MetaTrader: один терминал может торговать только ОДНИМ счётом
за раз. Поэтому:

  * счёт со своей копией терминала (указан terminal_path) получает
    отдельный процесс и работает ПАРАЛЛЕЛЬНО с остальными;
  * счета без своей копии обслуживаются ПО ОЧЕРЕДИ одним процессом,
    который переключается между ними.

Интерфейс общается только с этим модулем и никогда не вызывает MT5 сам —
поэтому окно не подвисает, даже если брокер отвечает медленно.
"""

from __future__ import annotations

import multiprocessing as mp
import queue
import time
import traceback
from dataclasses import asdict, dataclass, field

try:
    import MetaTrader5 as mt5

    MT5_AVAILABLE = True
except ImportError:  # не Windows или пакет не установлен
    mt5 = None
    MT5_AVAILABLE = False

# Если от процесса нет вестей дольше этого времени — считаем его зависшим
STALE_SECONDS = 10.0

# Список пар брокера обновляем редко: он почти не меняется, а запрос тяжёлый
SYMBOLS_REFRESH_SECONDS = 300.0
# У некоторых брокеров больше тысячи пар — столько гонять через очередь
# в интерфейс незачем, берём торгуемые и ограничиваем список
MAX_SYMBOLS = 600

# Команды из интерфейса в процесс
CMD_STOP = "stop"
CMD_CLOSE_ALL = "close_all"
CMD_CLOSE_PROFITABLE = "close_profitable"
CMD_CLOSE_LOSING = "close_losing"
CMD_CLOSE_ONE = "close_one"


@dataclass
class AccountState:
    """Снимок состояния счёта для интерфейса."""

    login: int = 0
    name: str = ""
    connected: bool = False
    status: str = "не запущен"
    error: str = ""
    balance: float = 0.0
    equity: float = 0.0
    margin_free: float = 0.0
    profit: float = 0.0
    positions: list = field(default_factory=list)
    available_symbols: list = field(default_factory=list)   # пары, доступные у брокера
    day_start_equity: float = 0.0
    daily_pct: float = 0.0
    trading_blocked: bool = False
    blocked_reason: str = ""
    updated_at: float = 0.0


def _position_to_dict(p) -> dict:
    return {
        "ticket": int(p.ticket),
        "symbol": p.symbol,
        "type": "BUY" if p.type == 0 else "SELL",
        "volume": float(p.volume),
        "price_open": float(p.price_open),
        "price_current": float(p.price_current),
        "sl": float(p.sl),
        "tp": float(p.tp),
        "profit": float(p.profit),
        "magic": int(getattr(p, "magic", 0)),
        "time": int(p.time),
    }


class AccountRunner:
    """Обслуживание одного или нескольких счетов в одном процессе.

    Если счетов несколько (общий терминал) — переключается между ними
    по кругу, потому что одновременно терминал держит только один счёт.
    """

    def __init__(self, accounts: list[dict], state_q, cmd_q):
        self.accounts = accounts
        self.state_q = state_q
        self.cmd_q = cmd_q
        self.running = True
        self.states: dict[int, AccountState] = {
            int(a["login"]): AccountState(login=int(a["login"]), name=a.get("name", ""))
            for a in accounts
        }
        self.day_start: dict[int, float] = {}
        self.day_serial = 0
        self.current_login = 0
        self.symbols_fetched_at: dict = {}

    # ---------- связь с интерфейсом ----------
    def publish(self, login: int) -> None:
        state = self.states[login]
        state.updated_at = time.time()
        try:
            self.state_q.put_nowait(state)
        except queue.Full:
            pass  # интерфейс не успевает читать — пропускаем кадр, это нормально

    def set_status(self, login: int, status: str, error: str = "") -> None:
        self.states[login].status = status
        self.states[login].error = error
        self.publish(login)

    # ---------- подключение ----------
    def switch_to(self, account: dict) -> bool:
        """Входит в указанный счёт. Для общего терминала это переключение."""
        login = int(account["login"])
        if self.current_login == login:
            return True

        if not MT5_AVAILABLE:
            self.set_status(login, "ошибка", "Пакет MetaTrader5 не установлен (нужен Windows)")
            return False

        kwargs = {
            "login": login,
            "password": account["password"],
            "server": account["server"],
        }
        if account.get("terminal_path"):
            kwargs["path"] = account["terminal_path"]

        ok = mt5.initialize(**kwargs)
        if not ok:
            # Некоторые версии терминала требуют login() отдельным вызовом
            if mt5.initialize(path=kwargs.get("path")):
                ok = mt5.login(login, password=account["password"], server=account["server"])
        if not ok:
            code, message = mt5.last_error()
            self.states[login].connected = False
            self.set_status(login, "ошибка входа", f"{message} (код {code})")
            self.current_login = 0
            return False

        info = mt5.account_info()
        if info is None or int(info.login) != login:
            self.set_status(login, "ошибка", "терминал не переключился на этот счёт")
            self.current_login = 0
            return False

        self.current_login = login
        self.states[login].connected = True
        self.set_status(login, "подключён")
        return True

    def disconnect(self) -> None:
        if MT5_AVAILABLE:
            try:
                mt5.shutdown()
            except Exception:  # noqa: BLE001
                pass
        self.current_login = 0

    # ---------- дневное состояние ----------
    def update_day(self, login: int, equity: float) -> None:
        serial = int(time.time() // 86400)
        if serial != self.day_serial:
            self.day_serial = serial
            self.day_start.clear()
        if login not in self.day_start:
            self.day_start[login] = equity

        state = self.states[login]
        state.day_start_equity = self.day_start[login]
        if state.day_start_equity > 0:
            state.daily_pct = (equity - state.day_start_equity) / state.day_start_equity * 100.0

    def check_daily_limit(self, login: int, account: dict) -> None:
        # Значение по умолчанию — 0, то есть «порога нет». Раньше здесь стояло
        # 3.0: счёт из старого accounts.json, где поля ещё не было, получал
        # дневную остановку, которую никто не включал.
        limit = float(account.get("daily_loss_percent", 0.0) or 0.0)
        state = self.states[login]
        if limit <= 0 or state.day_start_equity <= 0 or state.trading_blocked:
            return
        if state.daily_pct <= -abs(limit):
            state.trading_blocked = True
            state.blocked_reason = (
                f"дневной лимит убытка {limit:.1f}% достигнут "
                f"(сейчас {state.daily_pct:.2f}%)"
            )
            self.close_where(lambda p: True)
            self.publish(login)

    # ---------- список пар брокера ----------
    def refresh_symbols(self, login: int) -> None:
        """Спрашивает у терминала, какие пары доступны на ЭТОМ счёте.

        У разных брокеров разные имена одной и той же пары (EURUSD, EURUSDs,
        EURUSD.a), поэтому список нужно брать у самого брокера, а не угадывать.
        """
        last = self.symbols_fetched_at.get(login, 0.0)
        if time.time() - last < SYMBOLS_REFRESH_SECONDS:
            return
        self.symbols_fetched_at[login] = time.time()
        try:
            symbols = mt5.symbols_get()
        except Exception:  # noqa: BLE001
            return
        if not symbols:
            return
        names = []
        for item in symbols:
            # Пары, по которым торговать нельзя, в списке не нужны
            if getattr(item, "trade_mode", 1) == 0:
                continue
            names.append(item.name)
            if len(names) >= MAX_SYMBOLS:
                break
        self.states[login].available_symbols = sorted(names)

    # ---------- чтение состояния ----------
    def refresh(self, account: dict) -> None:
        login = int(account["login"])
        info = mt5.account_info()
        if info is None:
            self.states[login].connected = False
            self.set_status(login, "нет связи", "account_info вернул пусто")
            self.current_login = 0
            return

        state = self.states[login]
        state.balance = float(info.balance)
        state.equity = float(info.equity)
        state.margin_free = float(info.margin_free)

        rows = [_position_to_dict(p) for p in (mt5.positions_get() or [])]
        state.positions = rows
        state.profit = sum(r["profit"] for r in rows)

        self.refresh_symbols(login)
        self.update_day(login, state.equity)
        self.check_daily_limit(login, account)
        self.publish(login)

    # ---------- закрытие позиций ----------
    def close_ticket(self, ticket: int) -> bool:
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False
        p = positions[0]
        tick = mt5.symbol_info_tick(p.symbol)
        if tick is None:
            return False
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": int(p.ticket),
            "symbol": p.symbol,
            "volume": float(p.volume),
            "type": mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if p.type == 0 else tick.ask,
            "deviation": 20,
            "type_filling": mt5.ORDER_FILLING_FOK,
            "comment": "multi-account close",
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            # Часть брокеров не принимает FOK — пробуем IOC
            request["type_filling"] = mt5.ORDER_FILLING_IOC
            result = mt5.order_send(request)
        return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE

    def close_where(self, predicate) -> int:
        closed = 0
        for p in mt5.positions_get() or []:
            if predicate(p) and self.close_ticket(int(p.ticket)):
                closed += 1
        return closed

    # ---------- команды ----------
    def handle_command(self, cmd: dict) -> None:
        kind = cmd.get("kind")
        if kind == CMD_STOP:
            self.running = False
            return

        login = int(cmd.get("login", 0))
        account = next((a for a in self.accounts if int(a["login"]) == login), None)
        if account is None:
            return
        # Команда всегда выполняется на СВОЁМ счёте: сначала переключаемся
        if not self.switch_to(account):
            return

        if kind == CMD_CLOSE_ALL:
            n = self.close_where(lambda p: True)
            self.set_status(login, f"закрыто позиций: {n}")
        elif kind == CMD_CLOSE_PROFITABLE:
            n = self.close_where(lambda p: p.profit > 0)
            self.set_status(login, f"закрыто прибыльных: {n}")
        elif kind == CMD_CLOSE_LOSING:
            n = self.close_where(lambda p: p.profit < 0)
            self.set_status(login, f"закрыто убыточных: {n}")
        elif kind == CMD_CLOSE_ONE:
            ok = self.close_ticket(int(cmd["ticket"]))
            self.set_status(login, "позиция закрыта" if ok else "не удалось закрыть")

    def drain_commands(self) -> None:
        while True:
            try:
                cmd = self.cmd_q.get_nowait()
            except queue.Empty:
                return
            try:
                self.handle_command(cmd)
            except Exception as exc:  # noqa: BLE001
                login = int(cmd.get("login", 0)) or next(iter(self.states), 0)
                if login in self.states:
                    self.set_status(login, "ошибка команды", str(exc))

    # ---------- главный цикл ----------
    def run(self) -> None:
        interval = max(10, min(int(a.get("poll_interval_ms", 100)) for a in self.accounts)) / 1000.0
        try:
            while self.running:
                started = time.perf_counter()
                self.drain_commands()
                if not self.running:
                    break

                for account in self.accounts:
                    if not self.running:
                        break
                    if not self.switch_to(account):
                        continue
                    try:
                        self.refresh(account)
                    except Exception as exc:  # noqa: BLE001
                        self.set_status(int(account["login"]), "сбой опроса", str(exc))

                # Спим ровно остаток интервала, а не фиксированное время —
                # иначе медленный ответ брокера растягивал бы период опроса
                elapsed = time.perf_counter() - started
                time.sleep(max(0.0, interval - elapsed))
        finally:
            self.disconnect()
            for login in self.states:
                self.states[login].connected = False
                self.set_status(login, "остановлен")


def runner_entry(accounts: list, state_q, cmd_q) -> None:
    """Точка входа процесса."""
    try:
        AccountRunner(accounts, state_q, cmd_q).run()
    except Exception:  # noqa: BLE001
        for account in accounts:
            state = AccountState(
                login=int(account.get("login", 0)),
                status="аварийная остановка",
                error=traceback.format_exc(limit=3),
            )
            try:
                state_q.put_nowait(state)
            except Exception:  # noqa: BLE001
                pass


def _spawn(accounts: list, state_q, cmd_q):
    process = mp.Process(
        target=runner_entry, args=(accounts, state_q, cmd_q), daemon=True,
        name="mt5-" + ",".join(str(a["login"]) for a in accounts),
    )
    process.start()
    return process


class AccountSupervisor:
    """Управление всеми счетами: запуск, остановка, сбор состояния."""

    def __init__(self, spawn_fn=_spawn):
        # spawn_fn подменяется в тестах, чтобы не запускать настоящие процессы
        self._spawn = spawn_fn
        self._groups: dict[str, dict] = {}   # ключ группы -> {process, cmd_q, logins}
        self._state_q = mp.Queue(maxsize=2000)
        self._states: dict[int, AccountState] = {}
        self._group_of: dict[int, str] = {}

    # ---------- запуск ----------
    def start(self, accounts: list) -> tuple[int, list[str]]:
        """Запускает переданные счета. Возвращает (сколько запущено, сообщения)."""
        messages: list[str] = []
        ready = []
        for account in accounts:
            problems = account.validate()
            if problems:
                messages.append(f"{account.display()}: {', '.join(problems)}")
                continue
            if account.login in self._group_of:
                messages.append(f"{account.display()}: уже запущен")
                continue
            ready.append(account)

        if not ready:
            return 0, messages

        # Каждый счёт со своей копией терминала — отдельный процесс.
        # Все остальные делят один процесс и опрашиваются по очереди.
        groups: dict[str, list] = {}
        for account in ready:
            key = f"own:{account.login}" if account.runs_in_parallel() else "shared"
            groups.setdefault(key, []).append(account)

        started = 0
        for key, group in groups.items():
            cmd_q = mp.Queue(maxsize=200)
            dicts = [asdict(a) for a in group]
            process = self._spawn(dicts, self._state_q, cmd_q)
            self._groups[key] = {"process": process, "cmd_q": cmd_q,
                                 "logins": [a.login for a in group]}
            for account in group:
                self._group_of[account.login] = key
                state = AccountState(login=account.login, name=account.name,
                                     status="запускается")
                state.updated_at = time.time()
                self._states[account.login] = state
                started += 1

        if len(groups.get("shared", [])) > 1:
            messages.append(
                f"Счетов без своей копии терминала: {len(groups['shared'])} — "
                "они опрашиваются по очереди. Чтобы работали параллельно, "
                "укажите каждому свой terminal64.exe."
            )
        return started, messages

    # ---------- остановка ----------
    def stop_group(self, key: str, timeout: float = 5.0) -> None:
        group = self._groups.get(key)
        if group is None:
            return
        try:
            group["cmd_q"].put_nowait({"kind": CMD_STOP})
        except queue.Full:
            pass
        process = group["process"]
        if hasattr(process, "join"):
            process.join(timeout)
            if hasattr(process, "is_alive") and process.is_alive():
                process.terminate()  # не завершился сам — снимаем принудительно
        for login in group["logins"]:
            self._group_of.pop(login, None)
            state = self._states.get(login)
            if state is not None:
                state.connected = False
                state.status = "остановлен"
        self._groups.pop(key, None)

    def stop(self, login: int) -> None:
        key = self._group_of.get(login)
        if key is None:
            return
        group = self._groups[key]
        if len(group["logins"]) == 1:
            self.stop_group(key)
            return
        # Счёт делит процесс с другими: останавливаем группу и поднимаем остальных
        remaining = [lg for lg in group["logins"] if lg != login]
        self.stop_group(key)
        state = self._states.get(login)
        if state is not None:
            state.status = "остановлен"
        return remaining  # вызывающий код решит, поднимать ли оставшиеся

    def stop_all(self) -> None:
        for key in list(self._groups.keys()):
            self.stop_group(key)

    def is_running(self, login: int) -> bool:
        key = self._group_of.get(login)
        if key is None:
            return False
        process = self._groups[key]["process"]
        if hasattr(process, "is_alive"):
            return bool(process.is_alive())
        return True

    # ---------- состояние ----------
    def pump(self) -> list:
        """Забирает накопившиеся снимки. Вызывается интерфейсом по таймеру."""
        updated = []
        while True:
            try:
                state = self._state_q.get_nowait()
            except queue.Empty:
                break
            self._states[state.login] = state
            updated.append(state)
        return updated

    def state(self, login: int) -> AccountState:
        return self._states.get(login, AccountState(login=login))

    def all_states(self) -> dict:
        return dict(self._states)

    def is_stale(self, login: int, now: float | None = None) -> bool:
        if not self.is_running(login):
            return False
        state = self._states.get(login)
        if state is None or state.updated_at <= 0:
            return False
        now = time.time() if now is None else now
        return (now - state.updated_at) > STALE_SECONDS

    def totals(self) -> dict:
        balance = equity = profit = 0.0
        positions = connected = 0
        for login, state in self._states.items():
            if not self.is_running(login):
                continue
            balance += state.balance
            equity += state.equity
            profit += state.profit
            positions += len(state.positions)
            if state.connected:
                connected += 1
        return {
            "balance": balance, "equity": equity, "profit": profit,
            "positions": positions, "connected": connected,
            "running": sum(1 for lg in self._group_of if self.is_running(lg)),
        }

    # ---------- команды ----------
    def _send(self, login: int, command: dict) -> bool:
        key = self._group_of.get(login)
        if key is None:
            return False
        command = dict(command, login=login)
        try:
            self._groups[key]["cmd_q"].put_nowait(command)
            return True
        except queue.Full:
            return False

    def close_all(self, login: int) -> bool:
        return self._send(login, {"kind": CMD_CLOSE_ALL})

    def close_profitable(self, login: int) -> bool:
        return self._send(login, {"kind": CMD_CLOSE_PROFITABLE})

    def close_losing(self, login: int) -> bool:
        return self._send(login, {"kind": CMD_CLOSE_LOSING})

    def close_ticket(self, login: int, ticket: int) -> bool:
        return self._send(login, {"kind": CMD_CLOSE_ONE, "ticket": ticket})

    def close_all_everywhere(self) -> int:
        """Аварийная кнопка: закрыть всё на всех запущенных счетах."""
        return sum(1 for login in list(self._group_of.keys()) if self.close_all(login))
