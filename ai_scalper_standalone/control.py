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

import logging
import queue
import threading

import incident

log = logging.getLogger("control")

MAX_EQUITY_HISTORY = 200


class Control:
    def __init__(self):
        self._lock = threading.Lock()
        self._paused = False

        # НЕЗАКРЫТЫЙ ИНЦИДЕНТ ИСПОЛНЕНИЯ. Отдельно от обычной паузы, и это
        # различие principal: обычную паузу человек ставит и снимает когда
        # хочет, а инцидент означает «положение дел на счёте неизвестно» и
        # снимается только явным именным подтверждением.
        #
        # В памяти держится флаг, а не файл: is_paused() зовут по разу на
        # каждый инструмент на каждом проходе, читать диск столько раз ни к
        # чему. Флаг и файл меняются только вместе.
        self._инцидент = False
        self._инцидент_сведения: dict = {}
        self._инцидент_на_диске = True   # False — записать не удалось
        self._risk_profile = None   # None = использовать cfg.RISK_PROFILE по умолчанию
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
        """Запрещены ли НОВЫЕ входы.

        Две разные причины дают один ответ: обычная пауза с пульта и
        незакрытый инцидент. Для главного цикла разницы нет — нельзя
        значит нельзя. Разница есть для того, кто пытается снять."""
        with self._lock:
            return self._paused or self._инцидент

    def set_paused(self, value: bool):
        """Обычная пауза с пульта.

        ИНЦИДЕНТ ЭТА КНОПКА НЕ СНИМАЕТ. Иначе вся работа И2 сводилась бы к
        нулю одним нажатием: человек снял паузу, не поняв, что она стояла
        из-за невыясненной заявки, и программа пошла торговать поверх
        возможной неучтённой позиции."""
        with self._lock:
            self._paused = value
            остался = self._инцидент
        if остался and not value:
            log.warning("Пауза снята с пульта, но НЕЗАКРЫТЫЙ ИНЦИДЕНТ "
                        "остаётся — новые входы по-прежнему запрещены. "
                        "Снять его можно только подтверждением: %s",
                        self._инцидент_сведения.get("причина", ""))

    # --- НЕЗАКРЫТЫЙ ИНЦИДЕНТ ----------------------------------------
    def перечитать_инцидент(self, folder: str = "") -> bool:
        """Прочитать отметку с диска. Зовётся ОДИН раз при старте.

        Здесь и происходит главное, ради чего сделан И2: если программу
        перезапустили с незакрытым инцидентом, она об этом узнаёт и не
        начинает торговать."""
        есть = incident.открыт(folder)
        сведения = incident.подробности(folder) if есть else {}
        with self._lock:
            self._инцидент = есть
            self._инцидент_сведения = сведения
            self._инцидент_на_диске = True
        if есть:
            log.error("ПРИ ЗАПУСКЕ НАЙДЕН НЕЗАКРЫТЫЙ ИНЦИДЕНТ: %s. Новые "
                      "входы запрещены, пока человек его не снимет.",
                      сведения.get("причина", "подробности не читаются"))
        return есть

    def открыть_инцидент(self, сведения: dict, folder: str = "") -> bool:
        """Остановить торговлю до разбирательства. True — записано на диск.

        False значит, что запись не удалась. Остановка при этом ВСЁ РАВНО
        действует — просто она не переживёт перезапуск, и об этом надо
        сказать человеку."""
        записано = incident.открыть(сведения, folder)
        with self._lock:
            self._инцидент = True
            self._инцидент_сведения = dict(сведения or {})
            self._инцидент_на_диске = записано
        return записано

    def инцидент_открыт(self) -> bool:
        with self._lock:
            return self._инцидент

    def инцидент(self) -> dict:
        with self._lock:
            return dict(self._инцидент_сведения)

    def инцидент_на_диске(self) -> bool:
        """False — остановка действует, но перезапуск её снимет."""
        with self._lock:
            return self._инцидент_на_диске

    def сверить_инцидент(self):
        """Собрать факты по счёту и вынести вердикт. Ничего не меняет.

        Отдельно от снятия намеренно: посмотреть, что произошло, должно
        быть безопасно и не иметь последствий.

        Сверка подгружается ЗДЕСЬ, а не наверху файла. Она тянет за собой
        терминал, а вся остановочная часть пульта обязана работать и без
        него: запрет должен держаться даже там, где MetaTrader5 не
        установлен вовсе."""
        import reconcile
        return reconcile.выяснить(self.инцидент())

    def разрешить_инцидент(self, кто: str, комментарий: str = "",
                           folder: str = ""):
        """Снять инцидент ПО ДОКАЗАТЕЛЬСТВУ. Возвращает (снят, вердикт).

        Сначала программа сама идёт и смотрит, что на счёте: позиции,
        активные заявки, история заявок и сделок. Снимает только если
        положение дел ДОКАЗАНО.

        «Позиций сейчас не видно» доказательством не является — по той же
        причине, что и в И1-C: заявка может быть ещё активна. Любая
        неполнота или неоднозначность сохраняет запрет."""
        вердикт = self.сверить_инцидент()
        if not вердикт.доказан:
            log.error("Снять инцидент нельзя: %s", вердикт.состояние)
            for факт in вердикт.факты:
                log.error("  %s", факт)
            return False, вердикт

        подпись = f"{комментарий} [{вердикт.состояние}]".strip()
        снят = self.снять_инцидент(кто, подпись, folder,
                                   по_доказательству=True)
        return снят, вердикт

    def снять_инцидент(self, кто: str, комментарий: str = "",
                       folder: str = "", по_доказательству: bool = False) -> bool:
        """Закрыть инцидент. Только по явному ИМЕННОМУ действию человека.

        Программа сама этого не делает нигде — проверяется тестом.

        БЕЗ доказательства это ПРИНУДИТЕЛЬНОЕ снятие: человек берёт на
        себя ответственность за то, что разобрался. В журнале оно так и
        помечается — чтобы через месяц было видно, снимали по фактам или
        на глазок."""
        if not incident.снять(кто, комментарий, folder,
                              по_доказательству=по_доказательству):
            return False
        with self._lock:
            self._инцидент = False
            self._инцидент_сведения = {}
            self._инцидент_на_диске = True
        return True

    def get_risk_profile(self):
        with self._lock:
            return self._risk_profile

    def set_risk_profile(self, profile):
        with self._lock:
            self._risk_profile = profile

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
