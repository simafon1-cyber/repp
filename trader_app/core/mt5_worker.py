"""Рабочий процесс одного торгового счёта.

Один терминал MetaTrader 5 может торговать только ОДНИМ счётом одновременно —
это ограничение самого MetaTrader. Поэтому на каждый счёт запускается
отдельный процесс со своей копией терминала.

Процесс делает три вещи:
  1. Подключается к своей копии MT5 и входит в счёт.
  2. Опрашивает состояние (баланс, позиции) с заданным интервалом.
  3. Выполняет команды из очереди: открыть, закрыть, закрыть всё.

Всё общение с интерфейсом идёт через очереди, поэтому окно программы
никогда не подвисает из-за медленного ответа брокера.
"""

from __future__ import annotations

import queue
import time
import traceback
from dataclasses import dataclass, field
from multiprocessing import Process, Queue

try:
    import MetaTrader5 as mt5

    MT5_AVAILABLE = True
except ImportError:  # не Windows или пакет не установлен
    mt5 = None
    MT5_AVAILABLE = False


# --- Сообщения от процесса в интерфейс ---
@dataclass
class AccountState:
    """Снимок состояния счёта, отправляется в интерфейс."""

    login: int = 0
    connected: bool = False
    status: str = "не запущен"
    error: str = ""
    balance: float = 0.0
    equity: float = 0.0
    margin_free: float = 0.0
    profit: float = 0.0            # плавающий результат по открытым позициям
    positions: list[dict] = field(default_factory=list)
    day_start_equity: float = 0.0
    daily_pct: float = 0.0         # изменение за день, %
    trading_blocked: bool = False
    blocked_reason: str = ""
    updated_at: float = 0.0


# --- Команды из интерфейса в процесс ---
CMD_STOP = "stop"
CMD_CLOSE_ALL = "close_all"
CMD_CLOSE_PROFITABLE = "close_profitable"
CMD_CLOSE_LOSING = "close_losing"
CMD_CLOSE_ONE = "close_one"
CMD_OPEN = "open"
CMD_SET_AUTOTRADE = "set_autotrade"


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
        "time": int(p.time),
    }


class Mt5Worker:
    """Логика процесса. Вынесена в класс, чтобы её можно было тестировать."""

    def __init__(self, account_dict: dict, state_queue: Queue, cmd_queue: Queue):
        self.acc = account_dict
        self.state_q = state_queue
        self.cmd_q = cmd_queue
        self.state = AccountState(login=account_dict.get("login", 0))
        self.autotrade = False
        self.running = True
        self.day_start_equity = 0.0
        self.day_serial = 0

    # ---------- связь с интерфейсом ----------
    def publish(self) -> None:
        self.state.updated_at = time.time()
        try:
            self.state_q.put_nowait(self.state)
        except queue.Full:
            pass  # интерфейс не успевает читать — пропускаем кадр, это нормально

    def set_status(self, status: str, error: str = "") -> None:
        self.state.status = status
        self.state.error = error
        self.publish()

    # ---------- подключение ----------
    def connect(self) -> bool:
        if not MT5_AVAILABLE:
            self.set_status("ошибка", "Пакет MetaTrader5 не установлен (нужен Windows)")
            return False

        path = self.acc.get("terminal_path") or None
        kwargs = {
            "login": int(self.acc["login"]),
            "password": self.acc["password"],
            "server": self.acc["server"],
        }
        if path:
            kwargs["path"] = path

        if not mt5.initialize(**kwargs):
            code, message = mt5.last_error()
            self.set_status("ошибка входа", f"{message} (код {code})")
            return False

        info = mt5.account_info()
        if info is None:
            self.set_status("ошибка", "Терминал подключён, но счёт недоступен")
            return False

        self.state.connected = True
        self.set_status("подключён")
        return True

    def disconnect(self) -> None:
        if MT5_AVAILABLE:
            try:
                mt5.shutdown()
            except Exception:  # noqa: BLE001
                pass
        self.state.connected = False

    # ---------- дневное состояние ----------
    def update_day(self, equity: float) -> None:
        serial = int(time.time() // 86400)
        if serial != self.day_serial:
            self.day_serial = serial
            self.day_start_equity = equity
        self.state.day_start_equity = self.day_start_equity
        if self.day_start_equity > 0:
            self.state.daily_pct = (equity - self.day_start_equity) / self.day_start_equity * 100.0

    def check_daily_limit(self) -> None:
        limit = float(self.acc.get("daily_loss_percent", 3.0))
        if limit <= 0 or self.day_start_equity <= 0:
            return
        if self.state.daily_pct <= -abs(limit):
            if not self.state.trading_blocked:
                self.state.trading_blocked = True
                self.state.blocked_reason = (
                    f"дневной лимит убытка {limit:.1f}% достигнут "
                    f"(сейчас {self.state.daily_pct:.2f}%)"
                )
                self.close_all()  # закрываем всё и больше не открываем сегодня

    # ---------- чтение состояния ----------
    def refresh(self) -> None:
        info = mt5.account_info()
        if info is None:
            self.state.connected = False
            self.set_status("нет связи", "account_info вернул пусто")
            return

        self.state.balance = float(info.balance)
        self.state.equity = float(info.equity)
        self.state.margin_free = float(info.margin_free)

        positions = mt5.positions_get()
        rows = [_position_to_dict(p) for p in (positions or [])]
        self.state.positions = rows
        self.state.profit = sum(r["profit"] for r in rows)

        self.update_day(self.state.equity)
        self.check_daily_limit()
        self.publish()

    # ---------- торговые действия ----------
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
            "comment": "TraderApp close",
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

    def close_all(self) -> int:
        return self.close_where(lambda p: True)

    # ---------- команды ----------
    def handle_command(self, cmd: dict) -> None:
        kind = cmd.get("kind")
        if kind == CMD_STOP:
            self.running = False
        elif kind == CMD_CLOSE_ALL:
            n = self.close_all()
            self.set_status(f"закрыто позиций: {n}")
        elif kind == CMD_CLOSE_PROFITABLE:
            n = self.close_where(lambda p: p.profit > 0)
            self.set_status(f"закрыто прибыльных: {n}")
        elif kind == CMD_CLOSE_LOSING:
            n = self.close_where(lambda p: p.profit < 0)
            self.set_status(f"закрыто убыточных: {n}")
        elif kind == CMD_CLOSE_ONE:
            ok = self.close_ticket(int(cmd["ticket"]))
            self.set_status("позиция закрыта" if ok else "не удалось закрыть позицию")
        elif kind == CMD_SET_AUTOTRADE:
            self.autotrade = bool(cmd.get("value"))
            self.set_status("автоторговля включена" if self.autotrade else "автоторговля выключена")

    def drain_commands(self) -> None:
        while True:
            try:
                cmd = self.cmd_q.get_nowait()
            except queue.Empty:
                return
            try:
                self.handle_command(cmd)
            except Exception as exc:  # noqa: BLE001
                self.set_status("ошибка команды", str(exc))

    # ---------- главный цикл ----------
    def run(self) -> None:
        if not self.connect():
            return
        interval = max(10, int(self.acc.get("poll_interval_ms", 100))) / 1000.0
        try:
            while self.running:
                started = time.perf_counter()
                self.drain_commands()
                if not self.running:
                    break
                try:
                    self.refresh()
                except Exception as exc:  # noqa: BLE001
                    self.set_status("сбой опроса", str(exc))
                # Спим ровно остаток интервала, а не фиксированное время —
                # иначе медленный ответ брокера растягивал бы период опроса
                elapsed = time.perf_counter() - started
                time.sleep(max(0.0, interval - elapsed))
        finally:
            self.disconnect()
            self.set_status("остановлен")


def worker_entry(account_dict: dict, state_queue: Queue, cmd_queue: Queue) -> None:
    """Точка входа процесса."""
    try:
        Mt5Worker(account_dict, state_queue, cmd_queue).run()
    except Exception:  # noqa: BLE001
        state = AccountState(
            login=account_dict.get("login", 0),
            status="аварийная остановка",
            error=traceback.format_exc(limit=3),
        )
        try:
            state_queue.put_nowait(state)
        except Exception:  # noqa: BLE001
            pass


def start_worker(account_dict: dict, state_queue: Queue, cmd_queue: Queue) -> Process:
    process = Process(
        target=worker_entry,
        args=(account_dict, state_queue, cmd_queue),
        daemon=True,
        name=f"mt5-{account_dict.get('login')}",
    )
    process.start()
    return process
