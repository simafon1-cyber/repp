"""
control.py — потокобезопасный "пульт управления", общий между главным циклом
торговли (main.py, поток №1) и веб-дашбордом (web_dashboard.py, поток №2 — Flask).

ВАЖНО: все реальные вызовы MetaTrader5 (открытие/закрытие/модификация ордеров)
должны выполняться ТОЛЬКО из главного потока (main.py). Дашборд не дёргает
MT5 напрямую — он кладёт "заявки" в очередь (например, закрыть тикет) или
меняет "эффективные" настройки (профиль/режим), а главный цикл сам их читает
и исполняет на каждой итерации. Так исключаются гонки/краши от параллельных
обращений к API терминала из разных потоков.

Здесь же хранится история equity (для мини-графика на дашборде) — это просто
числа в памяти, без обращения к MT5, поэтому её можно писать из любого потока.
"""

import queue
import threading

MAX_EQUITY_HISTORY = 200


class Control:
    def __init__(self):
        self._lock = threading.Lock()
        self._paused = False
        self._risk_profile = None   # None = использовать cfg.RISK_PROFILE по умолчанию
        self._trading_mode = None   # None = использовать cfg.TRADING_MODE по умолчанию
        self.close_requests: "queue.Queue[int]" = queue.Queue()
        self._close_all_requested = False
        self._close_profitable_requested = False
        self._close_losing_requested = False
        self._equity_history: list = []  # [{"t": "12:34:56", "equity": 945.69}, ...]

        # Выбор пары (вкл/выкл торговлю новыми сделками) и фиксированный лот —
        # управляется с дашборда, без перезапуска. Пусто = "не переопределено":
        # символ по умолчанию включён, лот считается по риск-профилю.
        self._enabled_symbols: dict = {}   # symbol -> bool
        self._lot_overrides: dict = {}     # symbol -> float (0/None = авторасчёт)
        self._last_config_reload: str = ""

        # Уведомления (открытие/закрытие сделки, серия убытков и т.д.) — главный
        # цикл кладёт сюда события, desktop_app.py их забирает и показывает
        # звуком/всплывающим окном. Просто очередь строк в памяти.
        self._notifications: "queue.Queue[tuple]" = queue.Queue()

        # Пароль входа — ТОЛЬКО в памяти этого процесса, никогда не пишется
        # на диск. Нужен, чтобы расшифровывать секреты config.py (см.
        # secure_store.py) заново после каждого hot-reload конфига — и в
        # desktop-приложении, и при прямом запуске `python main.py`.
        self._session_password = None

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def set_paused(self, value: bool):
        with self._lock:
            self._paused = value

    def get_risk_profile(self):
        with self._lock:
            return self._risk_profile

    def set_risk_profile(self, profile):
        with self._lock:
            self._risk_profile = profile

    def get_trading_mode(self):
        with self._lock:
            return self._trading_mode

    def set_trading_mode(self, mode):
        with self._lock:
            self._trading_mode = mode

    def request_close(self, ticket: int):
        self.close_requests.put(int(ticket))

    def request_close_all(self):
        """Кнопка "Закрыть все сделки" — закрывает АБСОЛЮТНО ВСЕ открытые
        позиции счёта (бота и открытые вручную), исполняется в главном потоке
        (см. process_close_requests в main.py), как и обычное закрытие по тикету."""
        with self._lock:
            self._close_all_requested = True

    def is_close_all_requested(self) -> bool:
        with self._lock:
            return self._close_all_requested

    def clear_close_all_requested(self):
        with self._lock:
            self._close_all_requested = False

    def request_close_profitable(self):
        """Кнопка "Закрыть прибыльные" — закрывает ВСЕ открытые позиции счёта,
        у которых профит сейчас >= 0 (бота и открытые вручную), рынком."""
        with self._lock:
            self._close_profitable_requested = True

    def is_close_profitable_requested(self) -> bool:
        with self._lock:
            return self._close_profitable_requested

    def clear_close_profitable_requested(self):
        with self._lock:
            self._close_profitable_requested = False

    def request_close_losing(self):
        """Кнопка "Закрыть убыточные" — закрывает ВСЕ открытые позиции счёта,
        у которых профит сейчас < 0 (бота и открытые вручную), рынком."""
        with self._lock:
            self._close_losing_requested = True

    def is_close_losing_requested(self) -> bool:
        with self._lock:
            return self._close_losing_requested

    def clear_close_losing_requested(self):
        with self._lock:
            self._close_losing_requested = False

    def add_equity_sample(self, t: str, equity: float):
        with self._lock:
            self._equity_history.append({"t": t, "equity": equity})
            if len(self._equity_history) > MAX_EQUITY_HISTORY:
                self._equity_history.pop(0)

    def get_equity_history(self) -> list:
        with self._lock:
            return list(self._equity_history)

    def is_symbol_enabled(self, symbol: str) -> bool:
        with self._lock:
            return self._enabled_symbols.get(symbol, True)

    def set_symbol_enabled(self, symbol: str, enabled: bool):
        with self._lock:
            self._enabled_symbols[symbol] = enabled

    def get_enabled_symbols(self) -> dict:
        with self._lock:
            return dict(self._enabled_symbols)

    def get_lot_override(self, symbol: str):
        with self._lock:
            return self._lot_overrides.get(symbol)

    def set_lot_override(self, symbol: str, lot):
        with self._lock:
            if lot is None or lot <= 0:
                self._lot_overrides.pop(symbol, None)
            else:
                self._lot_overrides[symbol] = lot

    def get_lot_overrides(self) -> dict:
        with self._lock:
            return dict(self._lot_overrides)

    def set_last_config_reload(self, t: str):
        with self._lock:
            self._last_config_reload = t

    def get_last_config_reload(self) -> str:
        with self._lock:
            return self._last_config_reload

    def set_session_password(self, password):
        with self._lock:
            self._session_password = password

    def get_session_password(self):
        with self._lock:
            return self._session_password

    def push_notification(self, title: str, message: str):
        self._notifications.put((title, message))

    def drain_notifications(self) -> list:
        """Забирает и очищает все накопленные уведомления (вызывается из GUI)."""
        items = []
        while not self._notifications.empty():
            try:
                items.append(self._notifications.get_nowait())
            except queue.Empty:
                break
        return items


control = Control()
