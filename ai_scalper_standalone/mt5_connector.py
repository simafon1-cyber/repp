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
from datetime import datetime, timezone
import os

import MetaTrader5 as mt5

import config as cfg
import pretrade_gate

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


# =====================================================================
# ОКНО ТЕРМИНАЛА: УБРАТЬ С ГЛАЗ
# =====================================================================
# Владелец: «можно сделать, чтобы не открывался сам терминал MetaTrader,
# чтобы оно подключалось к учётной записи и работало... чтобы я мог просто
# ввести учётные данные и работать в этой же программе».
#
# ЧТО ТУТ ВОЗМОЖНО, А ЧТО НЕТ — честно.
#
# Убрать терминал СОВСЕМ нельзя, и это не вопрос усилий. Терминал и есть
# соединение с брокером: он держит защищённый канал, знает протокол обмена и
# отвечает за исполнение приказов. Наша программа с брокером не разговаривает
# вовсе — она отдаёт команды терминалу, а тот уже идёт к брокеру. Протокол
# этого канала закрытый, MetaQuotes его не публикует, и написать «свой
# терминал» нельзя ни за день, ни за год.
#
# А вот сделать его НЕВИДИМЫМ можно, и это ровно то, что человеку и нужно.
# Программа и так запускает терминал сама и сама входит в счёт (см. connect()
# ниже: логин, пароль и сервер передаются прямо в mt5.initialize). Остаётся
# только убрать окно. Терминал при этом продолжает работать полностью: он
# просто не показывается.
#
# ЧЕГО ЗДЕСЬ ОСТЕРЕГАЕМСЯ. Спрятанное окно исчезает и с панели задач — вернуть
# его мышью человек уже не сможет. Поэтому: есть обратная команда, окно
# показывается назад при выходе из программы, и в настройке это описано.
_MT5_WINDOW_CLASSES = (
    "MetaQuotes::MetaTrader::5",   # основное окно терминала
)
# Имена самого файла терминала. Ищем окно ЕЩЁ И ПО НИМ, а не только по классу
# окна: у владельца терминал был открыт и виден в диспетчере задач, а поиск по
# классу окна его не находил — «Терминал не найден». Класс окна зависит от
# сборки MetaTrader и может отличаться; имя файла — нет.
_MT5_PROCESS_NAMES = ("terminal64.exe", "terminal.exe")
_SW_HIDE = 0
_SW_SHOWNA = 8                     # показать, но не забирать фокус
_СПРОСИТЬ_НЕМНОГО = 0x1000         # PROCESS_QUERY_LIMITED_INFORMATION


def _process_name_of(pid: int) -> str:
    """Имя файла процесса по его номеру. Пустая строка — узнать не удалось."""
    if not pid:
        return ""
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(_СПРОСИТЬ_НЕМНОГО, False, int(pid))
        if not handle:
            return ""
        try:
            размер = ctypes.c_ulong(1024)
            буфер = ctypes.create_unicode_buffer(размер.value)
            if not kernel32.QueryFullProcessImageNameW(
                    handle, 0, буфер, ctypes.byref(размер)):
                return ""
            return os.path.basename(буфер.value).lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return ""


def set_terminal_visible(visible: bool) -> int:
    """Показать или спрятать окно терминала. Возвращает число окон, которых
    это коснулось. 0 — окон не нашлось (или мы не на Windows).

    Терминал от этого не останавливается: прячется только окно, процесс
    работает как работал.

    ОКНО ИЩЕТСЯ ДВУМЯ СПОСОБАМИ. По классу окна (быстро) И по имени файла
    процесса, которому окно принадлежит. Одного класса оказалось мало:
    владелец прислал снимок, где MetaTrader открыт и виден в диспетчере
    задач, а кнопка отвечала «Терминал не найден». Класс главного окна
    зависит от сборки терминала, имя файла terminal64.exe — нет."""
    try:
        import ctypes
        from ctypes import wintypes
    except (ImportError, ValueError):
        return 0                   # не Windows — прятать нечего
    if not hasattr(ctypes, "windll"):
        return 0

    user32 = ctypes.windll.user32
    touched = 0
    buffer = ctypes.create_unicode_buffer(256)
    # Имя файла процесса спрашиваем один раз на процесс: окон у терминала
    # много, а вызов не бесплатный.
    известные = {}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _param):
        nonlocal touched
        свой = False
        user32.GetClassNameW(hwnd, buffer, 256)
        if buffer.value in _MT5_WINDOW_CLASSES:
            свой = True
        else:
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            номер = pid.value
            if номер not in известные:
                известные[номер] = _process_name_of(номер)
            свой = известные[номер] in _MT5_PROCESS_NAMES
        if свой:
            user32.ShowWindow(hwnd, _SW_SHOWNA if visible else _SW_HIDE)
            touched += 1
        return True

    try:
        user32.EnumWindows(visit, 0)
    except Exception as e:          # noqa: BLE001
        log.debug("Не удалось перебрать окна: %s", e)
        return 0
    return touched


def hide_terminal() -> int:
    count = set_terminal_visible(False)
    if count:
        log.info("Окно терминала MetaTrader скрыто (%d шт). Терминал работает.", count)
    return count


def show_terminal() -> int:
    count = set_terminal_visible(True)
    if count:
        log.info("Окно терминала MetaTrader возвращено на экран (%d шт).", count)
    return count


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

    # Окно терминала убираем ПОСЛЕ успешного входа, а не до: пока вход не
    # состоялся, терминал может показывать окно с ошибкой или просить пароль,
    # и спрятать его означало бы спрятать от человека саму причину, по которой
    # ничего не работает.
    if getattr(cfg, "MT5_HIDE_TERMINAL", False):
        hide_terminal()
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


def select_symbol(symbol: str) -> str:
    """Добавить пару в «Обзор рынка» и сказать, ДОБАВИЛИ ли мы её.

    Разница важна. Программа добавляет пары сама, чтобы их замерить, — и
    обязана за собой убрать. Но убирать можно только то, что добавили МЫ:
    пары, которые человек открыл сам, трогать нельзя.

    Возвращает "добавлена" — её не было и мы добавили; "была" — уже была
    открыта; "" — не вышло."""
    info = mt5.symbol_info(symbol)
    if info is None:
        return ""
    if info.visible:
        return "была"
    if not mt5.symbol_select(symbol, True):
        return ""
    return "добавлена"


def deselect_symbol(symbol: str) -> bool:
    """Убрать пару из «Обзора рынка». Только для тех, кого добавили мы.

    MetaTrader сам не даст убрать пару с открытой позицией или открытым
    графиком — вернёт отказ, и это правильно."""
    try:
        return bool(mt5.symbol_select(symbol, False))
    except Exception as e:      # noqa: BLE001
        log.debug("Не удалось убрать %s из «Обзора рынка»: %s", symbol, e)
        return False


def get_rates_df(symbol: str, timeframe: str, count: int = 300):
    """Свечи от старых к новым. Последняя строка — последняя ЗАКРЫТАЯ свеча.

    =====================================================================
    ПОЧЕМУ ЗДЕСЬ ОТБРАСЫВАЕТСЯ ПОСЛЕДНЯЯ СВЕЧА — ЧИТАТЬ ОБЯЗАТЕЛЬНО
    =====================================================================
    copy_rates_from_pos(symbol, tf, 0, n) отдаёт свечи, начиная с позиции 0.
    Позиция 0 в MetaTrader — это ТЕКУЩАЯ, ещё не закрытая свеча. Она живёт и
    меняется до конца своего периода.

    Вся торговая логика программы написана в расчёте на противоположное.
    Прямая цитата из indicators.pullback_breakout_ok:

        «последняя строка = последняя ЗАКРЫТАЯ свеча (signal),
         предпоследняя = pullback»

    И советник на MQL5, с которого всё портировано, задаёт это явно
    (ai_scalper_pro/Config.mqh):

        #define SIGNAL_SHIFT 1   // анализируем последнюю ЗАКРЫТУЮ свечу
        #define PULLBACK_SHIFT 2 // свеча отката — на бар раньше сигнальной

    Python был сдвинут на один бар относительно советника: сигнальной свечой
    он считал формирующуюся, а свечой отката — последнюю закрытую.

    ЧЕМ ЭТО ОБОРАЧИВАЛОСЬ. Вход разрешён только на новом баре, а новый бар
    замечается в момент его ОТКРЫТИЯ. Опрос идёт раз в пять секунд, значит
    решение принималось по свече возрастом 0-5 секунд, у которой максимум и
    минимум почти совпадают:

      * подтверждение по свече (жёсткое вето) видело диапазон около нуля и
        возвращало «нет» -> score обнулялся -> вход запрещался. Это наиболее
        вероятная настоящая причина жалобы владельца «нету сделок»;
      * откат+пробой (20 баллов из 100) требует, чтобы сигнальная свеча
        перебила максимум предыдущей. Свеча за пять секунд этого почти
        никогда не делает — 20 баллов не начислялись;
      * фильтр истощения сравнивает диапазон свечи с ATR — при диапазоне
        около нуля он не срабатывал никогда;
      * True Range пустой свечи около нуля занижал ATR, а от ATR считается
        ширина стоп-лосса.

    Это НЕ заглядывание в будущее. Это ошибка противоположного знака:
    использовалась свеча, которой ещё нет, вместо той, что только что
    закрылась.

    Запрашиваем на одну свечу больше и отбрасываем последнюю: вызывающий код
    получает ровно столько баров, сколько просил, и все они закрытые."""
    import pandas as pd

    tf = TF_MAP[timeframe]
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count + 1)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    if len(df) > 1:
        # Единственная строка означала бы, что истории нет вовсе; отдать
        # пустой датафрейм хуже, чем отдать одну свечу.
        df = df.iloc[:-1].reset_index(drop=True)
    return df


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


def positions_or_none(symbol: str = None, magic: int = None):
    """То же самое, но ОТЛИЧАЕТ «позиций нет» от «спросить не удалось».

    get_open_positions() возвращает пустой список в обоих случаях, и для
    обычной работы этого хватает. Для СВЕРКИ не хватает категорически:
    после неясного ответа брокера пустой список означает либо «сделка не
    открылась» (можно спокойно идти дальше), либо «терминал молчит»
    (нельзя вообще ничего). Перепутать эти два — значит открыть вторую
    сделку поверх первой.

    None — «спросить не удалось». Список (возможно пустой) — ответ."""
    try:
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    except Exception as e:
        log.error("positions_get не отработал: %s", e)
        return None
    if positions is None:
        return None
    if magic is not None:
        positions = [p for p in positions if p.magic == magic]
    return list(positions)


def deals_or_none(date_from, date_to, symbol: str = None):
    """История сделок за период. None — спросить не удалось.

    Нужна там же, где positions_or_none: позиция могла открыться и тут же
    закрыться по стопу, и тогда среди открытых её нет, а в истории есть.

    ВРЕМЯ ЗДЕСЬ — ВСЕГДА UTC. Терминал отдаёт метки времени в UTC и
    запросы тоже понимает как UTC. Если передать сюда «наивный» datetime,
    построенный от местных часов, окно запроса уедет на разницу часовых
    поясов — у брокера это обычно 2–3 часа, у пользователя может быть
    любая. История вернётся пустой, и программа решит, что сделки не
    было, хотя она была. Поэтому наивное время здесь ДОСТРАИВАЕТСЯ до
    UTC, а не отдаётся терминалу как есть."""
    date_from = _в_utc(date_from)
    date_to = _в_utc(date_to)
    try:
        deals = (mt5.history_deals_get(date_from, date_to, group=symbol)
                 if symbol else mt5.history_deals_get(date_from, date_to))
    except Exception as e:
        log.error("history_deals_get не отработал: %s", e)
        return None
    if deals is None:
        return None
    return list(deals)


def _в_utc(момент):
    """datetime без часового пояса считается UTC, а не местным временем."""
    if isinstance(момент, datetime) and момент.tzinfo is None:
        return момент.replace(tzinfo=timezone.utc)
    return момент


def orders_or_none(symbol: str = None, magic: int = None):
    """АКТИВНЫЕ заявки. None — спросить не удалось.

    Заявка живёт отдельно от позиции. Между «отправлено» и «исполнено»
    она какое-то время просто ЛЕЖИТ на сервере, и в этот момент позиции
    ещё нет, сделки в истории ещё нет — а отменять или считать отказом
    нечего: заявка жива и вот-вот сработает.

    Без этого запроса отсутствие позиции и сделки выглядело как
    доказательство отказа. Это не доказательство, а наблюдение."""
    try:
        orders = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
    except Exception as e:
        log.error("orders_get не отработал: %s", e)
        return None
    if orders is None:
        return None
    if magic is not None:
        orders = [o for o in orders if getattr(o, "magic", None) == magic]
    return list(orders)


def order_by_ticket(ticket: int):
    """Активная заявка по номеру. None — спросить не удалось.

    Пустой список — заявки среди активных нет. Это НЕ значит «отменена»:
    она могла и исполниться. За ответом на это идут в историю заявок."""
    if not ticket or ticket <= 0:
        return None
    try:
        orders = mt5.orders_get(ticket=int(ticket))
    except Exception as e:
        log.error("orders_get(ticket=%s) не отработал: %s", ticket, e)
        return None
    if orders is None:
        return None
    return list(orders)


def history_order_by_ticket(ticket: int):
    """Завершённая заявка по номеру. None — спросить не удалось.

    Здесь и только здесь лежит окончательный ответ «чем всё кончилось»:
    исполнена, отменена, отклонена или истекла."""
    if not ticket or ticket <= 0:
        return None
    try:
        orders = mt5.history_orders_get(ticket=int(ticket))
    except Exception as e:
        log.error("history_orders_get(ticket=%s) не отработал: %s", ticket, e)
        return None
    if orders is None:
        return None
    return list(orders)


def deals_by_position(position_id: int):
    """Сделки ПО НОМЕРУ ПОЗИЦИИ. None — спросить не удалось.

    Нужна сверке после инцидента: по номеру позиции видно не только как
    она открывалась, но и закрывалась ли она вообще. Без этого «позиции
    нет» пришлось бы толковать наугад."""
    if not position_id or position_id <= 0:
        return None
    try:
        deals = mt5.history_deals_get(position=int(position_id))
    except Exception as e:
        log.error("history_deals_get(position=%s) не отработал: %s",
                  position_id, e)
        return None
    if deals is None:
        return None
    return list(deals)


def deals_by_order(ticket: int):
    """Сделки ПО НОМЕРУ ОРДЕРА. None — спросить не удалось.

    Единственный способ связать историю с КОНКРЕТНОЙ нашей заявкой.
    Поиск по инструменту и magic за интервал времени такой связи не даёт:
    в то же окно может попасть заявка второго экземпляра программы или
    сделка, открытая руками в терминале."""
    if not ticket or ticket <= 0:
        return None
    try:
        deals = mt5.history_deals_get(ticket=int(ticket))
    except Exception as e:
        log.error("history_deals_get(ticket=%s) не отработал: %s", ticket, e)
        return None
    if deals is None:
        return None
    return list(deals)


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


# =====================================================================
# УМЕЕТ ЛИ СЧЁТ ДЕРЖАТЬ ВСТРЕЧНЫЕ ПОЗИЦИИ
# =====================================================================
# Счета MT5 бывают двух видов, и это НЕ настройка программы, а свойство
# счёта у брокера:
#
#   RETAIL_HEDGING  по одному инструменту можно держать и покупку, и
#                   продажу одновременно — две отдельные позиции;
#   RETAIL_NETTING  позиция по инструменту всегда ОДНА. Встречный ордер не
#                   создаёт вторую, а уменьшает или разворачивает
#                   существующую.
#
# ЧЕМ ЭТО ОПАСНО. Настройка hedge_both_directions открывает BUY и SELL
# сразу. На hedging-счёте это две позиции, как и задумано. На netting-счёте
# вторая заявка ЗАКРОЕТ первую — и вместо хеджа получится закрытая сделка,
# а иногда и развёрнутая. Стратегия и учёт риска ожидают совсем другого.
#
# Проверки этого в программе не было вообще: поиск по всему проекту не
# находил ни NETTING, ни RETAIL_HEDGING, ни margin_mode.

def account_margin_mode(account=None):
    """Режим счёта числом или None, если узнать не удалось.

    None — это честный ответ «не знаю», а не «нетто» и не «хедж».
    Подставлять сюда предположение нельзя: на нём строится решение,
    открывать ли встречную позицию."""
    acc = account if account is not None else mt5.account_info()
    режим = getattr(acc, "margin_mode", None) if acc is not None else None
    if режим is None:
        return None
    try:
        return int(режим)
    except (TypeError, ValueError):
        return None


def hedging_block_reason(account=None) -> str:
    """Почему нельзя открывать встречные позиции. Пусто — можно.

    ОСТОРОЖНОСТЬ ЗДЕСЬ НАМЕРЕННО НЕСИММЕТРИЧНА. Если тип счёта выяснить не
    удалось, встречная позиция ЗАПРЕЩАЕТСЯ. Ошибка в эту сторону стоит
    одной неоткрытой сделки. Ошибка в другую — закрытой или развёрнутой
    позиции, которую никто не просил трогать."""
    хедж = getattr(mt5, "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING", None)
    if хедж is None:
        return ("Терминал не сообщает, какие бывают типы счетов — "
                "встречные позиции запрещены на всякий случай")

    режим = account_margin_mode(account)
    if режим is None:
        return ("Не удалось узнать тип счёта у терминала — встречные "
                "позиции запрещены, пока это неизвестно")
    if режим == хедж:
        return ""

    нетто = getattr(mt5, "ACCOUNT_MARGIN_MODE_RETAIL_NETTING", None)
    биржа = getattr(mt5, "ACCOUNT_MARGIN_MODE_EXCHANGE", None)
    имя = {нетто: "неттинговый (netting)",
           биржа: "биржевой (exchange)"}.get(режим, f"неизвестный ({режим})")
    return (f"Счёт {имя}: по инструменту может быть только ОДНА позиция. "
            f"Встречный ордер не создал бы вторую, а закрыл или развернул "
            f"бы первую. Выключите «хедж в обе стороны» в профиле риска.")


def send_market_order(symbol: str, direction: int, lot: float, sl_price: float, tp_price: float,
                       magic: int, comment: str = "", deviation: int = 20):
    # ПРЕДТОРГОВЫЙ БАРЬЕР. Стоит ПЕРВОЙ строкой, до чтения цены и до сборки
    # заявки: проверка, которую можно обойти, добравшись до кода ниже, —
    # не проверка. Бросает, а не возвращает None: возвращённое значение
    # можно не посмотреть, брошенную ошибку — нельзя. См. pretrade_gate.py.
    pretrade_gate.требовать("открытие позиции")
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
    pretrade_gate.требовать("изменение стопа или тейка")
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl": sl,
        "tp": tp,
    }
    return mt5.order_send(request)


def close_position_partial(position, volume: float):
    pretrade_gate.требовать("закрытие позиции")
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

# =====================================================================
# КОДЫ ОТВЕТА БРОКЕРА, КОТОРЫХ ЗДЕСЬ РАНЬШЕ НЕ БЫЛО
# =====================================================================
# Программа знала ровно четыре кода: DONE и три «повторите». Всё
# остальное считалось отказом. Для двух кодов это неправда, и неправда
# опасная:
#
#   DONE_PARTIAL (10010) — «исполнено ЧАСТИЧНО». Просили 0.10, дали 0.06.
#       Считать это отказом нельзя: позиция на счету ЕСТЬ. Режим
#       ORDER_FILLING_IOC, который здесь стоит по умолчанию, частичное
#       исполнение прямо разрешает — см. send_market_order().
#
#   TIMEOUT (10012) — «ответа нет». Не «отказ» и не «успех»: заявка могла
#       дойти до сервера и исполниться, а потерялся только ответ. Считать
#       это отказом — значит отправить вторую такую же.
#
# getattr с числом: в старых сборках модуля MetaTrader5 отдельных констант
# может не быть, а сами числа заданы платформой и не меняются.
RETCODE_DONE_PARTIAL = getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010)
RETCODE_TIMEOUT = getattr(mt5, "TRADE_RETCODE_TIMEOUT", 10012)
RETCODE_PLACED = getattr(mt5, "TRADE_RETCODE_PLACED", 10008)
RETCODE_CONNECTION = getattr(mt5, "TRADE_RETCODE_CONNECTION", 10031)
RETCODE_REJECT = getattr(mt5, "TRADE_RETCODE_REJECT", 10006)
RETCODE_NO_MONEY = getattr(mt5, "TRADE_RETCODE_NO_MONEY", 10019)
RETCODE_INVALID_FILL = getattr(mt5, "TRADE_RETCODE_INVALID_FILL", 10030)
RETCODE_HEDGE_PROHIBITED = getattr(mt5, "TRADE_RETCODE_HEDGE_PROHIBITED", 10046)

# Тип сделки в истории: 0 — вход в позицию, 1 — выход. Нужен сверке
# (trade_manager.сверить_вход): среди истории ищется именно ВХОД.
DEAL_ENTRY_IN = getattr(mt5, "DEAL_ENTRY_IN", 0)


# =====================================================================
# СОСТОЯНИЯ ЗАЯВКИ
# =====================================================================
# Числа заданы платформой (ENUM_ORDER_STATE) и не меняются. Нужны, чтобы
# отличить «заявка кончилась ничем» от «заявка кончилась сделкой» и от
# «заявка ещё жива». Раньше этого различия в программе не было вообще:
# отсутствие позиции считалось отказом, хотя заявка могла спокойно лежать
# на сервере и вот-вот исполниться.
ORDER_STATE_STARTED = getattr(mt5, "ORDER_STATE_STARTED", 0)
ORDER_STATE_PLACED = getattr(mt5, "ORDER_STATE_PLACED", 1)
ORDER_STATE_CANCELED = getattr(mt5, "ORDER_STATE_CANCELED", 2)
ORDER_STATE_PARTIAL = getattr(mt5, "ORDER_STATE_PARTIAL", 3)
ORDER_STATE_FILLED = getattr(mt5, "ORDER_STATE_FILLED", 4)
ORDER_STATE_REJECTED = getattr(mt5, "ORDER_STATE_REJECTED", 5)
ORDER_STATE_EXPIRED = getattr(mt5, "ORDER_STATE_EXPIRED", 6)

# Заявка ЗАКОНЧИЛАСЬ, и закончилась ничем. Только это доказывает отказ.
СОСТОЯНИЯ_БЕЗ_ИСПОЛНЕНИЯ = (ORDER_STATE_CANCELED, ORDER_STATE_REJECTED,
                            ORDER_STATE_EXPIRED)

# Заявка закончилась СДЕЛКОЙ — целиком или частью.
СОСТОЯНИЯ_С_ИСПОЛНЕНИЕМ = (ORDER_STATE_FILLED, ORDER_STATE_PARTIAL)

ИМЕНА_СОСТОЯНИЙ = {
    ORDER_STATE_STARTED: "проверяется",
    ORDER_STATE_PLACED: "размещена",
    ORDER_STATE_CANCELED: "отменена",
    ORDER_STATE_PARTIAL: "исполнена частично",
    ORDER_STATE_FILLED: "исполнена",
    ORDER_STATE_REJECTED: "отклонена",
    ORDER_STATE_EXPIRED: "истекла",
}


def имя_состояния(состояние) -> str:
    return ИМЕНА_СОСТОЯНИЙ.get(состояние, f"неизвестное ({состояние})")
