"""
mt5_connector.py — тонкая обёртка над пакетом MetaTrader5 (pip install MetaTrader5).

Два режима подключения (см. config.py, блок "ПОДКЛЮЧЕНИЕ К БРОКЕРУ"):
  1) MT5_LOGIN = 0 (по умолчанию) — подключается к УЖЕ ОТКРЫТОМУ и залогиненному
     терминалу MT5 на этом же компьютере, как и раньше.
  2) MT5_LOGIN задан — сама запускает терминал (path=MT5_TERMINAL_PATH, если
     указан) и логинится указанными login/password/server. Терминал MetaTrader 5
     всё равно должен быть УСТАНОВЛЕН на компьютере — просто не нужно держать
     его открытым и залогиненным заранее. Работает с ЛЮБЫМ брокером, у которого
     есть MT5-счёт — это не привязано к конкретному брокеру.
"""

import logging
import MetaTrader5 as mt5

import config as cfg

log = logging.getLogger("mt5_connector")

TF_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


def auto_login_account():
    """Данные для входа, если на вкладке «Брокер» они не заполнены.

    Владелец: «и автоматический вход в счёт». Счёт он добавлял на вкладке
    «Счета» — а главный торговый цикл входил только по полям вкладки
    «Брокер» (MT5_LOGIN/MT5_PASSWORD/MT5_SERVER). Получалось, что счёт в
    программе есть, но бот про него не знает и ждёт уже открытый терминал.

    Берём ПЕРВЫЙ полностью настроенный и включённый счёт из списка. Если
    список пуст, пароль не расшифрован или что-то не заполнено — возвращаем
    None, и всё работает как раньше: подключение к уже открытому терминалу.
    """
    try:
        import accounts as accounts_module
        from control import control
    except Exception:  # noqa: BLE001
        return None
    try:
        store = accounts_module.AccountStore()
        password = control.get_session_password() or ""
        salt = getattr(cfg, "SECURITY_SALT", "") or ""
        store.load(password, salt)
        ready = store.ready_accounts()
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось прочитать список счетов для автовхода: %s", e)
        return None
    return ready[0] if ready else None


def connect():
    kwargs = {}
    if getattr(cfg, "MT5_TERMINAL_PATH", ""):
        kwargs["path"] = cfg.MT5_TERMINAL_PATH

    login = int(getattr(cfg, "MT5_LOGIN", 0) or 0)
    password = getattr(cfg, "MT5_PASSWORD", "")
    server = getattr(cfg, "MT5_SERVER", "")

    if login <= 0:
        account = auto_login_account()
        if account is not None:
            log.info("Автовход: беру счёт %s (%s) со вкладки «Счета» — "
                     "на вкладке «Брокер» логин не заполнен.",
                     account.login, account.server)
            login, password, server = account.login, account.password, account.server
            if account.terminal_path and not kwargs.get("path"):
                kwargs["path"] = account.terminal_path

    use_credentials = login > 0
    if use_credentials:
        kwargs["login"] = int(login)
        kwargs["password"] = password
        kwargs["server"] = server

    ok = mt5.initialize(**kwargs)
    if not ok and use_credentials:
        # Иногда login нужно передавать отдельным вызовом login() уже ПОСЛЕ
        # initialize() (некоторые версии терминала капризничают, если всё
        # передавать одним вызовом) — пробуем запасной вариант.
        if mt5.initialize(path=kwargs.get("path")):
            ok = mt5.login(int(login), password=password, server=server)

    if not ok:
        hint = (
            f"Проверь логин/пароль/сервер в настройках подключения к брокеру "
            f"(сервер введён как '{server}')."
            if use_credentials else
            "Убедись, что терминал MetaTrader 5 уже ЗАПУЩЕН и ЗАЛОГИНЕН на этом компьютере."
        )
        raise RuntimeError(f"Не удалось подключиться к MT5: {mt5.last_error()}. {hint}")

    acc = mt5.account_info()
    if acc is None:
        raise RuntimeError("MT5 подключен, но account_info() пуст — терминал залогинен?")
    log.info(
        "Подключено к MT5. Счёт %s (%s), баланс %.2f %s",
        acc.login, acc.server, acc.balance, acc.currency,
    )
    return acc


def disconnect():
    mt5.shutdown()


def ensure_symbol(symbol: str) -> bool:
    info = mt5.symbol_info(symbol)
    if info is None:
        log.warning("Символ '%s' не найден у брокера — пропускаю.", symbol)
        return False
    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            log.warning("Не удалось добавить '%s' в Market Watch — пропускаю.", symbol)
            return False
    return True


def get_rates_df(symbol: str, timeframe: str, count: int = 300):
    import pandas as pd

    tf = TF_MAP[timeframe]
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def get_last_close(symbol: str, timeframe: str):
    df = get_rates_df(symbol, timeframe, count=2)
    if df is None or len(df) == 0:
        return None
    return df.iloc[-1]["time"]


def get_symbol_point(symbol: str) -> float:
    info = mt5.symbol_info(symbol)
    return info.point if info else 0.0001


def get_spread_points(symbol: str) -> int:
    info = mt5.symbol_info(symbol)
    return info.spread if info else 999999


def get_tick(symbol: str):
    return mt5.symbol_info_tick(symbol)


def get_price(symbol: str) -> float:
    """Текущая цена одной строкой — середина между bid и ask.

    Нужна там, где важен не точный уровень входа, а МАСШТАБ цены: например,
    чтобы посчитать, на какую сумму открывается позиция (см. потолок плеча в
    risk_manager.calc_lot). Для таких расчётов разница между bid и ask
    несущественна, а гадать, какую из двух брать, — лишний источник ошибок.

    0.0 — цены нет (нет связи, символ не выбран в обзоре рынка). Вызывающий
    обязан это проверить: считать что-либо от нулевой цены нельзя."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return 0.0
    bid = float(getattr(tick, "bid", 0) or 0)
    ask = float(getattr(tick, "ask", 0) or 0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return bid or ask or 0.0


def get_open_positions(symbol: str = None, magic: int = None):
    positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    if positions is None:
        return []
    if magic is not None:
        positions = [p for p in positions if p.magic == magic]
    return list(positions)


def trading_permission_status() -> dict:
    """Проверка разрешений на торговлю — самая частая причина того, что бот
    считает сигналы и не падает с ошибкой, но ни одна сделка не открывается.
    Ничего из этого не лечится настройками config.py — это настройки самого
    терминала/счёта MT5."""
    term = mt5.terminal_info()
    acc = mt5.account_info()
    problems = []
    if term is not None and not term.trade_allowed:
        problems.append(
            "В терминале MetaTrader 5 ВЫКЛЮЧЕНА кнопка «Algo Trading» (AutoTrading) "
            "на панели инструментов — включи её, должна стать зелёной."
        )
    if acc is not None and not acc.trade_allowed:
        problems.append(
            "Торговля запрещена на уровне СЧЁТА (инвесторский/read-only пароль, "
            "либо брокер временно ограничил торговлю на счёте)."
        )
    if acc is not None and not acc.trade_expert:
        problems.append(
            "В терминале выключено «Разрешить автоматическую торговлю» — "
            "Сервис -> Настройки -> Советники -> включить галочку."
        )
    return {
        "ok": len(problems) == 0,
        "problems": problems,
    }


def get_all_symbols() -> list:
    """Список ВСЕХ символов, доступных у брокера (mt5.symbols_get()) — не
    только тех, что уже добавлены в Market Watch. Нужен, чтобы предлагать
    пользователю выбор из РЕАЛЬНОГО списка при добавлении пары (см. вкладку
    «Символы»), а не свободный ввод текста — частая причина, что новая пара
    "не работает": опечатка или неверный суффикс (например, XAUUSD вместо
    XAUUSDs у брокеров, где золото идёт с суффиксом "s")."""
    symbols = mt5.symbols_get()
    if symbols is None:
        return []
    return sorted(s.name for s in symbols)


def get_deals_history(date_from, date_to):
    """Полная история сделок из MT5 (history_deals_get) за период — по ВСЕМ
    magic number, не только этого бота. Нужна для синхронизации статистики/
    журнала с реальной историей у брокера (в т.ч. сделки, открытые вручную
    в терминале)."""
    deals = mt5.history_deals_get(date_from, date_to)
    if deals is None:
        return []
    return list(deals)


def get_account_info():
    return mt5.account_info()


def send_market_order(symbol: str, direction: int, lot: float, sl_price: float, tp_price: float,
                       magic: int, comment: str = "", deviation: int = 20):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    price = tick.ask if direction == 1 else tick.bid
    order_type = mt5.ORDER_TYPE_BUY if direction == 1 else mt5.ORDER_TYPE_SELL

    info = mt5.symbol_info(symbol)
    filling = mt5.ORDER_FILLING_IOC
    if info is not None:
        # Автоопределение filling mode — та же идея, что и в EA (частая причина "тихих" отказов).
        # ВАЖНО: SYMBOL_FILLING_FOK/SYMBOL_FILLING_IOC — это флаги из MQL5 (ENUM_SYMBOL_TRADE_EXECUTION),
        # их НЕТ в Python-модуле MetaTrader5 (там только ORDER_FILLING_*), поэтому используем сами значения
        # флагов (1 = FOK, 2 = IOC) напрямую, а не несуществующий mt5.SYMBOL_FILLING_FOK/IOC.
        SYMBOL_FILLING_FOK = 1
        SYMBOL_FILLING_IOC = 2
        if info.filling_mode & SYMBOL_FILLING_FOK:
            filling = mt5.ORDER_FILLING_FOK
        elif info.filling_mode & SYMBOL_FILLING_IOC:
            filling = mt5.ORDER_FILLING_IOC

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": sl_price,
        "tp": tp_price,
        "deviation": deviation,
        "magic": magic,
        "comment": comment[:31],  # MT5 ограничивает длину комментария
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling,
    }
    return mt5.order_send(request)


def modify_position(ticket: int, sl: float, tp: float):
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl": sl,
        "tp": tp,
    }
    return mt5.order_send(request)


def close_position_partial(position, volume: float):
    direction_close = mt5.ORDER_TYPE_SELL if position.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(position.symbol)
    if tick is None:
        return None
    price = tick.bid if direction_close == mt5.ORDER_TYPE_SELL else tick.ask
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": position.symbol,
        "volume": volume,
        "type": direction_close,
        "position": position.ticket,
        "price": price,
        "deviation": 20,
        "magic": position.magic,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    return mt5.order_send(request)


def last_error():
    return mt5.last_error()


RETCODE_REQUOTE = mt5.TRADE_RETCODE_REQUOTE
RETCODE_PRICE_CHANGED = mt5.TRADE_RETCODE_PRICE_CHANGED
RETCODE_PRICE_OFF = mt5.TRADE_RETCODE_PRICE_OFF
RETCODE_DONE = mt5.TRADE_RETCODE_DONE
