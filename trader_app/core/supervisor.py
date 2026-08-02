"""Управление процессами счетов: запуск, остановка, сбор состояния.

Интерфейс общается только с этим классом и никогда — напрямую с MT5.
Благодаря этому окно не подвисает, даже если брокер отвечает медленно
или терминал завис.
"""

from __future__ import annotations

import multiprocessing as mp
import queue
import time
from dataclasses import asdict

from .accounts import Account
from .mt5_worker import (
    CMD_CLOSE_ALL,
    CMD_CLOSE_LOSING,
    CMD_CLOSE_ONE,
    CMD_CLOSE_PROFITABLE,
    CMD_SET_AUTOTRADE,
    CMD_STOP,
    AccountState,
    start_worker,
)

# Если от процесса нет вестей дольше этого времени — считаем его зависшим
STALE_SECONDS = 10.0


class Supervisor:
    def __init__(self, start_fn=start_worker):
        # start_fn подменяется в тестах, чтобы не запускать настоящие процессы
        self._start_fn = start_fn
        self._processes: dict[int, object] = {}
        self._cmd_queues: dict[int, mp.Queue] = {}
        self._state_queue: mp.Queue = mp.Queue(maxsize=1000)
        self._states: dict[int, AccountState] = {}

    # ---------- запуск и остановка ----------
    def start(self, account: Account) -> tuple[bool, str]:
        problems = account.validate()
        if problems:
            return False, "Счёт не настроен: " + ", ".join(problems)
        if self.is_running(account.login):
            return False, "Счёт уже запущен"

        cmd_q: mp.Queue = mp.Queue(maxsize=100)
        self._cmd_queues[account.login] = cmd_q
        self._processes[account.login] = self._start_fn(
            asdict(account), self._state_queue, cmd_q
        )
        state = AccountState(login=account.login, status="запускается")
        state.updated_at = time.time()
        self._states[account.login] = state
        return True, "Запущен"

    def stop(self, login: int, timeout: float = 5.0) -> None:
        cmd_q = self._cmd_queues.get(login)
        if cmd_q is not None:
            try:
                cmd_q.put_nowait({"kind": CMD_STOP})
            except queue.Full:
                pass
        process = self._processes.get(login)
        if process is not None and hasattr(process, "join"):
            process.join(timeout)
            if hasattr(process, "is_alive") and process.is_alive():
                process.terminate()  # не завершился сам — снимаем принудительно
        self._processes.pop(login, None)
        self._cmd_queues.pop(login, None)
        state = self._states.get(login)
        if state is not None:
            state.connected = False
            state.status = "остановлен"

    def stop_all(self) -> None:
        for login in list(self._processes.keys()):
            self.stop(login)

    def is_running(self, login: int) -> bool:
        process = self._processes.get(login)
        if process is None:
            return False
        if hasattr(process, "is_alive"):
            return bool(process.is_alive())
        return True

    # ---------- состояние ----------
    def pump(self) -> list[AccountState]:
        """Забирает накопившиеся снимки состояния. Вызывается интерфейсом по таймеру."""
        updated = []
        while True:
            try:
                state = self._state_queue.get_nowait()
            except queue.Empty:
                break
            self._states[state.login] = state
            updated.append(state)
        return updated

    def state(self, login: int) -> AccountState:
        return self._states.get(login, AccountState(login=login))

    def all_states(self) -> dict[int, AccountState]:
        return dict(self._states)

    def is_stale(self, login: int, now: float | None = None) -> bool:
        """Процесс запущен, но давно не отвечает."""
        if not self.is_running(login):
            return False
        state = self._states.get(login)
        if state is None or state.updated_at <= 0:
            return False
        now = time.time() if now is None else now
        return (now - state.updated_at) > STALE_SECONDS

    # ---------- сводка по всем счетам ----------
    def totals(self) -> dict:
        balance = equity = profit = 0.0
        positions = 0
        connected = 0
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
            "balance": balance,
            "equity": equity,
            "profit": profit,
            "positions": positions,
            "connected": connected,
            "running": sum(1 for lg in self._processes if self.is_running(lg)),
        }

    # ---------- команды ----------
    def _send(self, login: int, command: dict) -> bool:
        cmd_q = self._cmd_queues.get(login)
        if cmd_q is None:
            return False
        try:
            cmd_q.put_nowait(command)
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

    def set_autotrade(self, login: int, value: bool) -> bool:
        return self._send(login, {"kind": CMD_SET_AUTOTRADE, "value": value})

    def close_all_everywhere(self) -> int:
        """Аварийная кнопка: закрыть всё на всех счетах."""
        sent = 0
        for login in list(self._cmd_queues.keys()):
            if self.close_all(login):
                sent += 1
        return sent
