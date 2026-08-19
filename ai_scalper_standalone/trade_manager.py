"""
trade_manager.py — управление уже открытыми сделками (Break Even, ATR-трейлинг,
Profit Lock, частичное закрытие), отправка ордеров с повтором, CSV-лог.

1:1 портировано из TradeManager.mqh (AI Scalper Pro v8.0). Пиковая прибыль по
позиции (для Profit Lock) и список частично закрытых тикетов хранятся в
процессе как обычные dict/set по тикету — тикеты уникальны в рамках счёта,
поэтому не привязаны к конкретному символу.
"""

import csv
import logging
import math
import os
from datetime import datetime

import config as cfg
import mt5_connector as mt5c
import risk_manager as rm
import runtime_events
import safe_files

log = logging.getLogger("trade_manager")

_position_peak_points: dict = {}   # ticket -> peak profit, в пунктах
_partial_closed_tickets: set = set()
# Тикеты, по которым частичное закрытие включено и порог прибыли пройден, но
# закрыть нечего: половина минимального лота брокеру не отправляется. Нужен
# только чтобы написать об этом ОДИН раз на сделку, а не на каждом такте.
_partial_impossible_tickets: set = set()
# Тикеты, по которым брокер отказался переносить стоп/цель. Нужны, чтобы
# сказать об этом ОДИН раз на сделку, а не каждую секунду.
_modify_failed_tickets: set = set()
# ticket -> изначальный риск сделки (price_open<->SL при первом же взгляде на
# позицию, до того как BE/трейлинг/лок успели её подтянуть), в пунктах — она
# же "1R". См. update_position_risk() и диагноз в manage_open_positions().
_position_risk_points: dict = {}
# ticket -> худшая (самая отрицательная) прибыль в пунктах за жизнь позиции.
# Нужна для "спасения в безубыток": сделку, которая ГЛУБОКО уходила в минус и
# вернулась к нулю, разумнее отпустить в ноль, чем ждать полной цели — см.
# break_even_rescue_action().
_position_trough_points: dict = {}
# position_id -> пик прибыли ЗАКРЫТОЙ позиции. Архив на момент исчезновения
# позиции из списка открытых: main.process_closed_deals() забирает его оттуда
# и отдаёт в auto_learning.record_trade_peak() — так бот учится, сколько
# пунктов реально даёт рынок по этой паре. Размер ограничен, чтобы словарь не
# рос бесконечно в долгой сессии.
_closed_peaks: dict = {}
_CLOSED_PEAKS_LIMIT = 500
# ticket -> момент (по НАШИМ часам), когда мы впервые увидели эту позицию.
# Возраст сделки НЕЛЬЗЯ считать как now() - position.time: position.time —
# это время ТОРГОВОГО СЕРВЕРА брокера, которое отличается от местного на
# часы (у многих брокеров это UTC+2/+3, а зимой и летом по-разному). Разница
# в 3 часа превратила бы только что открытую сделку либо в "висит 3 часа"
# (мгновенное сжатие цели до пола), либо в отрицательный возраст. Свои часы
# дают честный возраст без единого допущения о часовом поясе брокера.
_position_first_seen: dict = {}
# ticket -> имя механизма, который ПОСЛЕДНИМ сдвинул стоп в сторону прибыли.
# Механизмов, двигающих стоп, четыре, и все они пишут в одну переменную: по
# итоговому числу невозможно сказать, кто его поставил. Нужно, чтобы у
# закрытой сделки была честная причина выхода, а не догадка (см. журнал
# выходов ниже и docs/EXIT_AUDIT.md).
_position_sl_source: dict = {}
# ticket -> кто последним подвинул ЦЕЛЬ. Отдельно от стопа: закрыть сделку
# может и стоп, и цель, а поставили их разные механизмы. Какой из двух
# сработал, знает только брокер — он и говорит это в причине сделки
# (см. exit_reason_for в main.py).
_position_tp_source: dict = {}
# ticket -> (сколько секунд от открытия до пика, до самой глубокой просадки).
# Пик и просадка сами по себе не говорят, РАНО или ПОЗДНО сделка их показала.
_position_peak_age: dict = {}
_position_trough_age: dict = {}
# ticket -> первоначальный стоп-лосс (цена). Отличается от _position_risk_points
# тем, что это цена, а не расстояние: нужна в журнале как есть.
_position_initial_sl: dict = {}
# position_id -> полная карточка ЗАКРЫВШЕЙСЯ сделки. Забирается из main.py при
# разборе закрытых сделок и пишется в журнал выходов. Тот же приём и тот же
# предел размера, что у _closed_peaks.
_closed_journal: dict = {}


# Заголовок журнала выходов. Порядок столбцов — ровно как в задании владельца.
ЖУРНАЛ_ВЫХОДОВ_СТОЛБЦЫ = [
    "time", "symbol", "ticket", "direction", "entry", "exit",
    "initial_sl", "initial_r_points", "max_profit_r", "max_loss_r",
    "time_to_mfe_sec", "time_to_mae_sec", "holding_time_sec",
    "exit_reason", "profit", "profit_r",
]

# Названия причин выхода. Заглавными и латиницей — это машинный столбец, по
# которому потом считают, а не текст для чтения человеком.
ПРИЧИНА_СТОП = "STOP_LOSS"
ПРИЧИНА_БЕЗУБЫТОК = "BREAK_EVEN"
ПРИЧИНА_ТРЕЙЛИНГ = "TRAILING"
ПРИЧИНА_ЛЕСТНИЦА = "R_LADDER"
ПРИЧИНА_ЛОК = "PROFIT_LOCK"
ПРИЧИНА_ЦЕЛЬ = "TAKE_PROFIT"
ПРИЧИНА_СПАСЕНИЕ = "BREAK_EVEN_RESCUE"
ПРИЧИНА_ЧАСТИЧНО = "PARTIAL_CLOSE"
ПРИЧИНА_РУЧНОЕ = "MANUAL"
ПРИЧИНА_НЕИЗВЕСТНО = "UNKNOWN"


def _ensure_csv_header():
    if not os.path.exists(cfg.LOG_CSV_PATH):
        with open(cfg.LOG_CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(
                ["Time", "Event", "Symbol", "Direction", "Price", "SL", "TP", "Lot", "Score", "Profit"]
            )
            f.flush()
            os.fsync(f.fileno())
        try:
            safe_files.restrict_to_current_user(cfg.LOG_CSV_PATH)
            safe_files.mark_integrity_current(cfg.LOG_CSV_PATH)
        except Exception:
            pass


def log_trade_csv(evt, symbol, direction, price, sl, tp, lot, score, profit=0.0):
    if not cfg.LIVE_TRADING:
        prefix = "[DRY-RUN] "
    else:
        prefix = ""
    _ensure_csv_header()

    def _write_row(f):
        writer = csv.writer(f, delimiter=";")
        writer.writerow(
            [datetime.now().isoformat(timespec="seconds"), prefix + evt, symbol, direction,
             f"{price:.5f}", f"{sl:.5f}", f"{tp:.5f}", f"{lot:.2f}", f"{score:.1f}", f"{profit:.2f}"]
        )

    try:
        # flush+fsync сразу после записи — строка сделки гарантированно
        # попадает на диск, а не теряется в кэше ОС при внезапном завершении
        # процесса; плюс обновляется sha256-сайдкар для проверки целостности
        # журнала сделок при следующем запуске (см. safe_files.py).
        safe_files.append_line_safely(cfg.LOG_CSV_PATH, _write_row)
    except Exception as e:
        log.warning("Не удалось безопасно дозаписать в %s (%s) — пробую обычной записью.",
                    cfg.LOG_CSV_PATH, e)
        with open(cfg.LOG_CSV_PATH, "a", newline="", encoding="utf-8") as f:
            _write_row(f)


def exit_journal_path() -> str:
    """Куда писать журнал выходов. Рядом с журналом сделок, если явно не задано."""
    свой = getattr(cfg, "EXIT_JOURNAL_PATH", "")
    if свой:
        return свой
    папка = os.path.dirname(os.path.abspath(cfg.LOG_CSV_PATH))
    return os.path.join(папка, "exits_log.csv")


def _ensure_journal_header(путь):
    if os.path.exists(путь):
        return
    with open(путь, "w", newline="", encoding="utf-8") as f:
        csv.writer(f, delimiter=";").writerow(ЖУРНАЛ_ВЫХОДОВ_СТОЛБЦЫ)
        f.flush()
        os.fsync(f.fileno())
    try:
        safe_files.restrict_to_current_user(путь)
        safe_files.mark_integrity_current(путь)
    except Exception:
        pass


def log_exit_journal(строка: dict):
    """Дописать одну закрытую сделку в журнал выходов.

    Журнал существует ради одного: чтобы по каждой закрытой сделке было
    ВИДНО, какой именно механизм её закрыл, насколько высоко она поднималась
    и насколько глубоко проседала — в долях собственного риска. Без этого
    любой разбор «почему прибыль маленькая» упирается в догадки.

    Ошибка записи журнала не должна ронять торговлю: это наблюдение, а не
    решение. Поэтому исключение только пишется в лог."""
    путь = exit_journal_path()
    try:
        _ensure_journal_header(путь)

        def _write_row(f):
            csv.writer(f, delimiter=";").writerow(
                [строка.get(имя, "") for имя in ЖУРНАЛ_ВЫХОДОВ_СТОЛБЦЫ])

        try:
            safe_files.append_line_safely(путь, _write_row)
        except Exception:
            with open(путь, "a", newline="", encoding="utf-8") as f:
                _write_row(f)
    except Exception as e:
        log.warning("Не удалось записать журнал выходов (%s): %s", путь, e)


# Что вернуть, когда сделка ТОЧНО открылась, а номер позиции брокер не
# сообщил. Это не «не открылось»: число истинно, и вызывающий код по-прежнему
# пишет `if ok:`. Но закрывать такую ногу придётся не по номеру, а по
# инструменту и направлению — см. close_leg().
TICKET_НЕИЗВЕСТЕН = -1


def execute_market_order(symbol, direction, lot, sl_dist, tp_dist, score, point):
    """Открыть сделку по рынку.

    Возвращает НОМЕР ПОЗИЦИИ (истинное число) при успехе и 0 при отказе.
    Раньше возвращалось True/False, и этого не хватало: у хеджа две ноги, и
    если вторая не исполнилась, первую надо закрыть — а закрыть без номера
    нечего. Проверка `if ok:` работает как прежде: ноль ложен, номер истинен."""
    dir_txt = "BUY" if direction == 1 else "SELL"

    # tp_dist <= 0 означает "без TP" (см. USE_MAX_PROFIT_RIDE в config.py) —
    # сделка едет, пока её не остановит BE/трейлинг/Profit Lock, а не
    # фиксированная цель прибыли. tp=0.0 — стандартное для MT5 значение
    # "тейк-профит не задан" (а НЕ "цена входа").
    if not cfg.LIVE_TRADING:
        log.info("[DRY-RUN] %s %s lot=%.2f score=%.1f (ордер НЕ отправлен, LIVE_TRADING=False)",
                  symbol, dir_txt, lot, score)
        tick = mt5c.get_tick(symbol)
        price = (tick.ask if direction == 1 else tick.bid) if tick else 0.0
        sl = price - sl_dist if direction == 1 else price + sl_dist
        tp = 0.0 if tp_dist <= 0 else (price + tp_dist if direction == 1 else price - tp_dist)
        log_trade_csv("OPEN", symbol, dir_txt, price, sl, tp, lot, score)
        return TICKET_НЕИЗВЕСТЕН      # проверка на истории: позиции нет

    for attempt in range(1, cfg.ORDER_RETRY_ATTEMPTS + 1):
        tick = mt5c.get_tick(symbol)
        if tick is None:
            return False
        price = tick.ask if direction == 1 else tick.bid
        sl = price - sl_dist if direction == 1 else price + sl_dist
        tp = 0.0 if tp_dist <= 0 else (price + tp_dist if direction == 1 else price - tp_dist)

        if not rm.check_stops_distance(symbol, price, sl, tp):
            log.warning("%s: SL/TP слишком близко, ордер отменён", symbol)
            return 0

        result = mt5c.send_market_order(symbol, direction, lot, sl, tp, cfg.MAGIC_NUMBER,
                                         comment="AI_Scalper_Standalone")
        if result is None:
            log.error("%s: order_send вернул None, %s", symbol, mt5c.last_error())
            return 0

        if result.retcode == mt5c.RETCODE_DONE:
            log.info("%s %s | Score %.1f | Попытка %d | тикет %s",
                      symbol, dir_txt, score, attempt, result.order)
            log_trade_csv("OPEN", symbol, dir_txt, price, sl, tp, lot, score)
            # Номер позиции у рыночного ордера совпадает с номером ордера.
            # Если брокер его почему-то не прислал — сделка всё равно
            # открыта, и это НЕ отказ: возвращаем метку «номер неизвестен».
            return int(getattr(result, "order", 0) or 0) or TICKET_НЕИЗВЕСТЕН

        log.warning("%s: ошибка %s — %s", symbol, result.retcode, result.comment)
        transient = result.retcode in (mt5c.RETCODE_REQUOTE, mt5c.RETCODE_PRICE_CHANGED, mt5c.RETCODE_PRICE_OFF)
        if not transient:
            return 0
    return 0


def close_leg(symbol: str, ticket: int, direction: int = 0,
              volume: float = 0.0) -> bool:
    """Закрыть одну ногу хеджа целиком. True — закрыта.

    ЗАЧЕМ. Хедж отправляется двумя заявками подряд, и это НЕ одна операция.
    Между ними меняется цена, свободная маржа и настроение брокера. Если
    первая нога исполнилась, а вторая нет, на счету остаётся ОДИНОЧНАЯ
    направленная позиция — не то, что просил владелец, и не то, под что
    считался риск. Такую ногу надо немедленно закрыть.

    Номер позиции может быть неизвестен (TICKET_НЕИЗВЕСТЕН): брокер не
    обязан его присылать. Тогда нога ищется по инструменту, направлению и
    объёму среди наших открытых позиций — иначе закрывать было бы нечего.

    В режиме проверки (LIVE_TRADING=False) ничего не отправляется: позиции
    не существует, закрывать нечего, и это успех, а не отказ."""
    if not cfg.LIVE_TRADING:
        log.debug("[DRY-RUN] %s: закрытие ноги %s не отправляется", symbol, ticket)
        return True
    try:
        позиции = mt5c.get_open_positions(symbol=symbol, magic=cfg.MAGIC_NUMBER)
    except Exception as e:
        log.error("%s: не удалось получить позиции для закрытия ноги: %s", symbol, e)
        return False

    нога = None
    if ticket and ticket != TICKET_НЕИЗВЕСТЕН:
        нога = next((p for p in позиции if p.ticket == ticket), None)
    if нога is None and direction:
        # Запасной путь: номера нет. Берём НАШУ позицию нужного направления,
        # подходящую по объёму. Если таких несколько — самую свежую: именно
        # её мы только что и открыли.
        import MetaTrader5 as mt5
        нужный_тип = mt5.POSITION_TYPE_BUY if direction == 1 else mt5.POSITION_TYPE_SELL
        похожие = [p for p in позиции if p.type == нужный_тип
                   and (volume <= 0 or abs(p.volume - volume) < 1e-9)]
        нога = max(похожие, key=lambda p: getattr(p, "time", 0), default=None)

    if нога is None:
        # Позиции нет. Это не обязательно беда: её мог уже забрать стоп.
        # Но и подтвердить закрытие мы не можем — отвечаем честно.
        log.warning("%s: нога %s для закрытия не найдена среди открытых позиций",
                    symbol, ticket)
        return False

    result = mt5c.close_position_partial(нога, нога.volume)
    ок = result is not None and getattr(result, "retcode", None) == mt5c.RETCODE_DONE
    if ок:
        note_manual_close(нога.ticket)
        log.info("%s: нога %s закрыта (компенсация неполного хеджа)",
                 symbol, нога.ticket)
    else:
        log.error("%s: НЕ УДАЛОСЬ закрыть ногу %s: %s", symbol, нога.ticket, result)
    return ок


def cleanup_peak_profit(open_tickets: set):
    """Забыть память о сделках, которых больше нет среди открытых.

    ВНИМАНИЕ, ГЛАВНОЕ ПРО ЭТУ ФУНКЦИЮ: open_tickets обязан содержать тикеты
    ВСЕХ открытых сделок бота, по всем инструментам сразу. Всё, чего в наборе
    нет, считается закрытым и стирается.

    Раньше её вызывала manage_open_positions(symbol, ...), а та знает только
    ОДИН инструмент — и передавала тикеты только этого инструмента. При двух и
    более открытых сделках по разным парам каждый проход стирал память обо
    всех остальных:

      * возраст сделки обнулялся -> поджим тейка по времени не двигался
        дальше первой минуты, а спасение в безубыток (10 минут) не
        наступало НИКОГДА;
      * пик прибыли обнулялся -> Profit Lock гнался за текущей ценой вместо
        пика, отступ от пика в лестнице трейлинга терял смысл;
      * риск сделки пересчитывался от ТЕКУЩЕГО стопа вместо исходного;
      * пик ещё живой сделки уходил в архив как «закрытая» и попадал в
        обучение — то есть автообучение училось на выдуманных сделках.

    В отчёте владельца одновременно бывало до 9 открытых сделок по 10 парам,
    а быстрый монитор ходит раз в секунду — значит стирание шло постоянно.
    Теперь уборка вызывается один раз за проход оттуда, где виден полный
    список позиций (см. main.py). Проверяется тестом
    test_multi_symbol_state_survives."""
    # СНАЧАЛА КАРТОЧКИ, ПОТОМ УБОРКА. Карточка закрывшейся сделки собирается
    # из пика, дна и риска — то есть ровно из того, что уборка ниже стирает.
    # Если поменять эти два шага местами, в журнал пойдут пустые поля: пик к
    # тому моменту уже выброшен. Поймано тестом test_exit_management.
    исчезли = set()
    for словарь in (_position_peak_points, _position_trough_points,
                    _position_risk_points, _position_first_seen):
        исчезли.update(t for t in словарь if t not in open_tickets)
    for ticket in исчезли:
        _archive_journal(ticket)

    for ticket in list(_position_peak_points.keys()):
        if ticket not in open_tickets:
            # Пик НЕ выбрасываем, а откладываем в архив: сделка закрылась, и
            # именно сейчас её пик становится обучающим примером для
            # auto_learning (см. pop_closed_peak и _closed_peaks выше).
            _archive_closed_peak(ticket, _position_peak_points.pop(ticket))
    for ticket in list(_partial_closed_tickets):
        if ticket not in open_tickets:
            _partial_closed_tickets.discard(ticket)
    for ticket in list(_partial_impossible_tickets):
        if ticket not in open_tickets:
            _partial_impossible_tickets.discard(ticket)
    for ticket in list(_modify_failed_tickets):
        if ticket not in open_tickets:
            _modify_failed_tickets.discard(ticket)
    for ticket in list(_position_risk_points.keys()):
        if ticket not in open_tickets:
            _position_risk_points.pop(ticket, None)
    for ticket in list(_position_trough_points.keys()):
        if ticket not in open_tickets:
            _position_trough_points.pop(ticket, None)
    for ticket in list(_position_first_seen.keys()):
        if ticket not in open_tickets:
            _position_first_seen.pop(ticket, None)
    for словарь in (_position_sl_source, _position_tp_source, _position_peak_age,
                    _position_trough_age, _position_initial_sl):
        for ticket in list(словарь.keys()):
            if ticket not in open_tickets:
                словарь.pop(ticket, None)


def _archive_closed_peak(position_id, peak_points: float):
    _closed_peaks[position_id] = peak_points
    while len(_closed_peaks) > _CLOSED_PEAKS_LIMIT:
        # dict в Python сохраняет порядок вставки — вычищаем самый старый
        _closed_peaks.pop(next(iter(_closed_peaks)), None)


def _archive_journal(ticket):
    """Сложить карточку закрывшейся сделки в архив.

    Пишется только то, что мы ДЕЙСТВИТЕЛЬНО измерили, пока вели позицию.
    Ничего не достраивается и не оценивается задним числом: пустое поле
    честнее выдуманного."""
    риск = _position_risk_points.get(ticket, 0.0)
    пик = _position_peak_points.get(ticket)
    дно = _position_trough_points.get(ticket)
    if пик is None and дно is None and риск <= 0:
        return                      # позиция открыта до запуска — измерять нечего
    _closed_journal[ticket] = {
        "initial_sl": _position_initial_sl.get(ticket, 0.0),
        "initial_r_points": round(риск, 1),
        "max_profit_r": round(пик / риск, 3) if (риск > 0 and пик is not None) else "",
        "max_loss_r": round(дно / риск, 3) if (риск > 0 and дно is not None) else "",
        "time_to_mfe_sec": int(_position_peak_age.get(ticket, 0)),
        "time_to_mae_sec": int(_position_trough_age.get(ticket, 0)),
        "holding_time_sec": int(position_age_seconds(ticket)),
        "exit_reason": _position_sl_source.get(ticket, ПРИЧИНА_СТОП),
        "tp_reason": _position_tp_source.get(ticket, ПРИЧИНА_ЦЕЛЬ),
    }
    while len(_closed_journal) > _CLOSED_PEAKS_LIMIT:
        _closed_journal.pop(next(iter(_closed_journal)), None)


def pop_closed_journal(position_id):
    """Карточка закрытой сделки или None. Забирается один раз — повторный
    вызов вернёт None, и дважды в журнал одна сделка не попадёт."""
    return _closed_journal.pop(position_id, None)


def note_manual_close(ticket):
    """Отметить, что сделку закрыли кнопкой, а не механизмом.

    Без этой отметки ручное закрытие выглядело бы в журнале как срабатывание
    того стопа, который стоял последним, — и статистика причин врала бы."""
    _position_sl_source[ticket] = ПРИЧИНА_РУЧНОЕ


def pop_closed_peak(position_id):
    """Пик прибыли закрытой позиции (в пунктах) или None, если он не
    отслеживался — например, позиция открылась до запуска программы."""
    return _closed_peaks.pop(position_id, None)


def update_peak_profit(ticket, profit_points) -> float:
    peak = _position_peak_points.get(ticket, profit_points)
    if ticket not in _position_peak_points or profit_points > peak:
        peak = profit_points
        # Запоминаем НЕ только величину пика, но и когда он случился. Без
        # этого невозможно отличить «сделка сразу пошла и мы её рано срезали»
        # от «сделка час болталась и дала пик перед самым закрытием».
        _position_peak_age[ticket] = position_age_seconds(ticket)
    _position_peak_points[ticket] = peak
    return peak


def update_position_risk(ticket, price_open: float, sl: float, point: float) -> float:
    """Запоминает изначальный риск сделки (расстояние price_open<->SL) ОДИН
    РАЗ, при первом же взгляде на позицию — до того как BE/трейлинг/Profit
    Lock успеют подтянуть SL ближе к цене. Это и есть "1R" сделки: сколько
    она может максимум потерять по своему первоначальному стопу.

    Зачем: Profit Lock раньше стартовал по ПОРОГУ ОТ ATR (PROFIT_LOCK_START_
    POINTS), никак не связанному с тем, насколько ШИРОКИЙ стоп у конкретного
    профиля. У "Истерички" (atr_sl_multiplier=0.5) это давало запуск лока
    уже на ~30% от риска сделки — РАНЬШЕ даже безубытка. Из-за этого сделка
    фиксировалась в крошечный плюс почти сразу после открытия (иногда за
    1-2 секунды на волатильном золоте), а при неудаче стоп отрабатывал на
    ПОЛНУЮ дистанцию — отсюда "мелкие плюсы, крупные минусы" в реальных
    сделках. См. PROFIT_LOCK_START_R_FRACTION в config.py."""
    if ticket not in _position_risk_points:
        risk = abs(price_open - sl) / point if (sl and point) else 0.0
        _position_risk_points[ticket] = risk
        _position_initial_sl[ticket] = float(sl or 0.0)
    return _position_risk_points[ticket]


def _tiered_lock_percent(peak_points: float, unit: float) -> float:
    """Ступенчатая фиксация: чем выше пик прибыли (в единицах порога
    PROFIT_LOCK_START_POINTS, авто-масштабированного под ATR — это и есть
    `unit`), тем больший % пика запирается стопом. Возвращает % САМОГО
    СТАРШЕГО тира, до которого дорос пик; если тиров нет/unit некорректен —
    падает обратно на плоский cfg.PROFIT_LOCK_PERCENT."""
    tiers = getattr(cfg, "PROFIT_LOCK_TIERS", None)
    if not tiers or unit <= 0:
        return cfg.PROFIT_LOCK_PERCENT
    best_pct = cfg.PROFIT_LOCK_PERCENT
    for mult, pct in sorted(tiers, key=lambda t: t[0]):
        if peak_points >= mult * unit:
            best_pct = pct
    return best_pct


def r_ladder_lock_points(peak_points: float, risk_points: float,
                         ladder=None, giveback_r: float = 0.0):
    """Сколько прибыли (в ПУНКТАХ от цены входа) обязан запереть стоп при
    данном пике. None — защита ещё не включилась, стоп не трогаем.

    ЗАЧЕМ ЭТО ПОЯВИЛОСЬ. Разбор реального отчёта владельца за 06-07.08.2026
    (211 сделок, счёт $65, итог -19.99) показал ровно одну развилку:

        стоп успел уйти в плюс  ->  74 сделки, +89.56
        стоп остался в минусе   -> 137 сделок, -109.55

    Живут обе группы одинаково (медиана 6.6 и 6.5 минуты) — дело не в том,
    что прибыльные сделки держали дольше. Весь результат счёта решает одно:
    успел ли стоп подтянуться в плюс, прежде чем цена развернулась.

    А включалась защита поздно. При поле стопа в 1.5 ATR (MIN_SL_ATR_FRACTION)
    собственный риск сделки R = 1.5 ATR, и пороги были такими:

        безубыток   1.0 ATR = 0.67R
        ATR-трейлинг 1.2 ATR = 0.80R, и отступ тоже 1.2 ATR = 0.80R
        Profit Lock  1.0R

    То есть НИЧЕГО не защищало сделку ниже 0.67R, а отступ трейлинга (0.80R)
    был почти равен самому стопу — подтянувшись, он всё равно отдавал назад
    почти всю дистанцию. Сделка могла сходить на +0.5R в вашу сторону,
    развернуться и забрать полный стоп: половина цели была в руках, а
    потеряли в три раза больше.

    Лестница считает не в ATR, а в R — в собственном риске ЭТОЙ сделки. Это
    и делает её предсказуемой: «прошли треть своего риска — стоп в
    безубыток» звучит одинаково и для золота, и для EURUSD, при любом
    профиле и любой ширине стопа.

    giveback_r — сколько R разрешено отдать от пика. Работает поверх
    лестницы и только ПОДНИМАЕТ фиксацию: на сильном движении стоп идёт за
    ценой, не дожидаясь следующей ступеньки.

    Функция ничего не двигает сама и не знает про buy/sell: она возвращает
    только «сколько запереть», а двигать стоп можно исключительно в сторону
    прибыли — за это отвечает _better_sl в manage_open_positions."""
    if risk_points <= 0:
        return None
    if ladder is None:
        ladder = getattr(cfg, "R_TRAIL_LADDER", None)
    if not ladder:
        return None

    peak_r = peak_points / risk_points
    lock_r = None
    for step in sorted(ladder, key=lambda t: float(t[0])):
        trigger_r, locked_r = float(step[0]), float(step[1])
        if peak_r + 1e-9 >= trigger_r:
            lock_r = locked_r
    if lock_r is None:
        return None                       # до первой ступеньки не доросли

    if giveback_r > 0:
        lock_r = max(lock_r, peak_r - giveback_r)
    # Запереть больше, чем сделка когда-либо стоила, нельзя: такой стоп
    # оказался бы ВПЕРЕДИ цены и сработал бы мгновенно по худшей стороне.
    lock_r = min(lock_r, peak_r)
    return lock_r * risk_points


def position_age_seconds(ticket, now: float = None) -> float:
    """Сколько секунд мы ведём эту позицию — по НАШИМ часам (см. комментарий
    к _position_first_seen: время брокера для этого не годится).

    Для позиции, открытой до запуска программы, отсчёт начинается с момента,
    когда мы её впервые увидели. Это занижает возраст, то есть ошибается в
    безопасную сторону: цель прибыли сожмётся позже, а не раньше времени."""
    if now is None:
        now = datetime.now().timestamp()
    first = _position_first_seen.setdefault(ticket, now)
    return max(0.0, now - first)


def update_position_trough(ticket, profit_points: float) -> float:
    """Запоминает самую глубокую просадку позиции (в пунктах, отрицательное
    число). Зеркало update_peak_profit()."""
    trough = _position_trough_points.get(ticket, profit_points)
    if ticket not in _position_trough_points or profit_points < trough:
        trough = profit_points
        _position_trough_age[ticket] = position_age_seconds(ticket)
    _position_trough_points[ticket] = trough
    return trough


# =====================================================================
# ПОДТЯГИВАНИЕ ТЕЙК-ПРОФИТА
# =====================================================================
# Правило проекта, зеркальное правилу для стоп-лосса: TP можно только
# ПРИБЛИЖАТЬ к цене, никогда не отодвигать дальше. Отодвинутый TP — это
# скрытое увеличение риска: сделка висит дольше, дольше платит своп и дольше
# подставляется под разворот. Приближение TP, наоборот, только ускоряет
# фиксацию прибыли, а хуже сделке от этого стать не может.
#
# Цель прибыли сжимается со временем: чем дольше сделка "не едет", тем
# скромнее цель, вплоть до пола TP_TIGHTEN_MIN_FRACTION. Так позиция не
# зависает на весь день в ожидании цели, которую рынок уже не даст.

def shrunk_target_points(target_points: float, age_seconds: float,
                         shrink_per_minute: float, min_fraction: float) -> float:
    """Цель прибыли, ужатая пропорционально возрасту сделки."""
    if target_points <= 0:
        return 0.0
    minutes = max(0.0, age_seconds) / 60.0
    fraction = 1.0 - shrink_per_minute * minutes
    fraction = max(min_fraction, min(1.0, fraction))
    return target_points * fraction


def tp_floor_price(is_buy: bool, price_open: float, price: float,
                   min_profit_points: float, point: float, broker_min_dist: float) -> float:
    """Ближайший TP, который вообще допустим:
      * не ближе min_profit_points от цены входа — TP всегда остаётся в плюсе,
        подтягивание не имеет права превратить цель прибыли в убыток;
      * не ближе минимальной дистанции брокера от ТЕКУЩЕЙ цены — иначе сервер
        отклонит изменение."""
    if is_buy:
        return max(price_open + min_profit_points * point, price + broker_min_dist)
    return min(price_open - min_profit_points * point, price - broker_min_dist)


def tighten_take_profit(is_buy: bool, price_open: float, price: float, current_tp: float,
                        target_points: float, min_profit_points: float, point: float,
                        broker_min_dist: float, step_points: float,
                        risk_points: float = 0.0, min_r: float = 1.0) -> float:
    """Новый TP или 0.0, если менять нечего.

    Возвращает 0.0 (а не current_tp) специально: вызывающий код так отличает
    "изменений нет" от "поставить TP в ноль", а ноль TP в MT5 означает
    "цель не задана" и здесь никогда не является результатом.

    risk_points — собственный риск сделки (расстояние вход<->стоп) в пунктах,
    min_r — сколько таких риском цель обязана покрывать."""
    if target_points <= 0 or point <= 0:
        return 0.0

    # КРИТИЧНО. Цель не может стать меньше собственного риска сделки.
    # Без этого пола получалось следующее: у профиля "Истеричка" стоп равен
    # 0.5*ATR, стартовая цель 1.5*ATR, а сжатие со временем опускало её до
    # 25% = 0.375*ATR. Через 7-8 минут сделка рисковала БОЛЬШЕ, чем могла
    # выиграть (1 : 0.75), и при обычном винрейте это гарантированный минус —
    # мелкие плюсы против полноразмерных стопов. Та же ошибка уже была здесь
    # с Profit Lock, её лечит PROFIT_LOCK_START_R_FRACTION; сжатие цели
    # обязано подчиняться тому же правилу.
    if risk_points > 0 and min_r > 0:
        target_points = max(target_points, risk_points * min_r)

    candidate = (price_open + target_points * point) if is_buy else (price_open - target_points * point)
    floor = tp_floor_price(is_buy, price_open, price, min_profit_points, point, broker_min_dist)
    candidate = max(candidate, floor) if is_buy else min(candidate, floor)

    if current_tp == 0:
        return candidate                      # цели не было — ставим первую

    closer = candidate < current_tp if is_buy else candidate > current_tp
    if not closer:
        return 0.0                            # дальше отодвигать запрещено
    # Эпсилон обязателен: 2001.0 - 2000.95 = 0.04999999999999982, и без допуска
    # перенос ровно на шаг отвергался бы как "мельче шага" (та же природа, что
    # и ошибка округления лота — см. FloorToStep в DualGuardEA.mq5).
    if abs(candidate - current_tp) + point * 1e-6 < step_points * point:
        return 0.0                            # мельче шага — не дёргаем сервер
    return candidate


# =====================================================================
# СПАСЕНИЕ УБЫТОЧНОЙ СДЕЛКИ В БЕЗУБЫТОК
# =====================================================================
# Сделка, которая заметно уходила в минус и вернулась к нулю, уже показала,
# что вход был неудачным: рынок сходил против неё. Ждать от такой сделки
# полной цели — значит чаще всего отдать обратно и этот возврат.
#
# ВАЖНО, чего этот механизм НЕ делает: он не трогает стоп-лосс, не отодвигает
# его и не отменяет. Он только добавляет ВЫХОД РАНЬШЕ. Если рынок не вернётся
# к нулю — сделку закроет обычный стоп-лосс на своей исходной дистанции.

def break_even_rescue_action(trough_points: float, profit_points: float, age_seconds: float,
                             min_drawdown_points: float, after_seconds: float,
                             exit_points: float) -> str:
    """"" (ничего не делать) / "arm" (подтянуть TP к нулю) / "close" (закрыть сейчас)."""
    if trough_points > -abs(min_drawdown_points):
        return ""                             # глубоко в минус не уходила — обычный режим
    if age_seconds < after_seconds:
        return ""                             # ещё рано, пусть сделка поработает
    if profit_points >= exit_points:
        return "close"                        # уже вернулась в плюс — забираем
    return "arm"


def _better_sl(is_buy: bool, a: float, b: float) -> float:
    if a == 0:
        return b
    if b == 0:
        return a
    return max(a, b) if is_buy else min(a, b)


def manage_open_positions(symbol: str, atr_value: float, point: float, positions=None,
                          learned_tp_points: float = 0.0):
    """Break Even / ATR-трейлинг / Profit Lock / частичное закрытие — на все
    открытые позиции этого символа с нашим magic number.

    positions: если передан уже готовый список (одним запросом на всю
    итерацию главного цикла — см. main.py), фильтруем его локально вместо
    отдельного обращения к MT5 на каждый символ. Иначе — запрашиваем сами,
    как раньше (для обратной совместимости с другими вызовами).

    learned_tp_points: выученная по прошлым сделкам цель прибыли в пунктах
    (auto_learning.learned_profit_points). 0 — данных ещё мало, цель берётся
    от ATR."""
    import MetaTrader5 as mt5

    if positions is None:
        positions = mt5c.get_open_positions(symbol=symbol, magic=cfg.MAGIC_NUMBER)
    else:
        positions = [p for p in positions if p.symbol == symbol and p.magic == cfg.MAGIC_NUMBER]
    # Уборки памяти о закрытых сделках здесь НЕТ намеренно: эта функция видит
    # только один инструмент, а уборка обязана знать про все сразу. Раньше
    # вызов стоял тут и стирал состояние чужих сделок на каждом проходе —
    # см. подробный разбор в cleanup_peak_profit(). Теперь её вызывает
    # main.py оттуда, где виден полный список открытых позиций.

    # Без размера пункта считать нечего: ниже на него ДЕЛИТСЯ прибыль в
    # пунктах. Ноль сюда приходит, когда терминал ещё не отдал описание
    # символа, и раньше это давало ZeroDivisionError прямо в цикле ведения
    # позиций — то есть ровно там, где владелец наблюдал «бот перестал
    # работать, помог перезапуск».
    if point <= 0:
        log.warning("%s: размер пункта неизвестен (%s) — веду позиции на следующем проходе",
                    symbol, point)
        return

    info = mt5.symbol_info(symbol)
    broker_min_dist = (info.trade_stops_level * point) if info else 0.0

    for p in positions:
        is_buy = p.type == mt5.POSITION_TYPE_BUY
        tick = mt5c.get_tick(symbol)
        if tick is None:
            continue
        price = tick.bid if is_buy else tick.ask
        profit_points = (price - p.price_open) / point if is_buy else (p.price_open - price) / point
        # ВОЗРАСТ СЧИТАЕТСЯ ПЕРВЫМ, И ЭТО НЕ КОСМЕТИКА. Именно здесь позиция
        # впервые попадает в _position_first_seen. update_peak_profit ниже
        # тоже спрашивает возраст (чтобы запомнить, КОГДА был пик), и если
        # пустить его вперёд, то отметка времени создастся там — а к этой
        # строке сделке будет уже несколько микросекунд от роду. Цель прибыли
        # ужимается пропорционально возрасту, и на первом же проходе она
        # оказывалась чуть меньше положенного. Поймано тестом
        # test_profit_taking.
        age_seconds = position_age_seconds(p.ticket)
        peak_points = update_peak_profit(p.ticket, profit_points)
        trough_points = update_position_trough(p.ticket, profit_points)
        risk_points = update_position_risk(p.ticket, p.price_open, p.sl, point)

        # 0) Частичное закрытие при достижении профита (один раз за сделку) —
        # как "Partial Close" в MQL5-советнике. Не мешает BE/трейлингу ниже —
        # они продолжают вести остаток позиции как обычно.
        if (getattr(cfg, "USE_PARTIAL_CLOSE", False) and p.ticket not in _partial_closed_tickets):
            eff_partial_trigger = rm.eff_points_threshold(
                getattr(cfg, "PARTIAL_CLOSE_TRIGGER_POINTS", 150), 0.6, atr_value, point)
            if profit_points >= eff_partial_trigger:
                close_pct = getattr(cfg, "PARTIAL_CLOSE_PERCENT", 50)
                close_volume = p.volume * close_pct / 100.0
                vol_min = info.volume_min if info else 0.01
                vol_step = info.volume_step if info else 0.01
                if vol_step > 0:
                    close_volume = math.floor(close_volume / vol_step) * vol_step
                close_volume = max(0.0, min(p.volume, close_volume))
                if close_volume < vol_min or close_volume <= 0:
                    # Молчать здесь нельзя. Настройка включена, прибыль до
                    # порога дошла — а закрытия не будет: половина от
                    # минимального лота (0.005 при 0.01) брокеру не
                    # отправишь. Человек будет ждать частичной фиксации,
                    # которой физически не может произойти. Пишем один раз
                    # на сделку, чтобы не залить лог повторами.
                    if p.ticket not in _partial_impossible_tickets:
                        _partial_impossible_tickets.add(p.ticket)
                        log.info(
                            "%s тикет %s: частичное закрытие невозможно — "
                            "%.0f%% от %.2f лота это %.3f, а брокер принимает "
                            "не меньше %.2f и шагом %.2f. Заработает при лоте "
                            "от %.2f (сейчас лот минимальный).",
                            symbol, p.ticket, close_pct, p.volume,
                            p.volume * close_pct / 100.0, vol_min, vol_step,
                            vol_min * 2)
                elif close_volume >= vol_min and close_volume > 0:
                    if cfg.LIVE_TRADING:
                        result = mt5c.close_position_partial(p, close_volume)
                        if result is not None and result.retcode == mt5c.RETCODE_DONE:
                            _partial_closed_tickets.add(p.ticket)
                            _position_sl_source[p.ticket] = ПРИЧИНА_ЧАСТИЧНО
                            log.info("%s тикет %s: частичное закрытие %.2f лота (профит %.1f пт)",
                                     symbol, p.ticket, close_volume, profit_points)
                        else:
                            log.warning("%s тикет %s: не удалось частично закрыть (%s)",
                                        symbol, p.ticket, result)
                    else:
                        log.debug("[DRY-RUN] %s тикет %s: частичное закрытие %.2f лота (профит %.1f пт)",
                                  symbol, p.ticket, close_volume, profit_points)
                        _partial_closed_tickets.add(p.ticket)

        current_sl = p.sl
        current_tp = p.tp
        best_sl = current_sl
        # КТО ДВИГАЕТ СТОП. Четыре механизма ниже пишут в одну переменную
        # best_sl, и по итоговому числу не сказать, чей это уровень. Здесь
        # запоминается имя того, кто реально подвинул стоп в сторону прибыли.
        #
        # Это же место отвечает за требование «механизмы не должны бороться
        # друг с другом»: бороться им не за что, потому что применяется не
        # последний по порядку, а САМЫЙ ВЫГОДНЫЙ из предложенных, и только
        # если он лучше текущего. Стоп физически не может уехать назад —
        # см. _better_sl и проверку improved ниже.
        sl_source = _position_sl_source.get(p.ticket, ПРИЧИНА_СТОП)

        def предложить(уровень, кто):
            """Предложить новый стоп. Принимается, только если он ЛУЧШЕ."""
            nonlocal best_sl, sl_source
            если_лучше = _better_sl(is_buy, best_sl, уровень)
            if если_лучше != best_sl:
                best_sl = если_лучше
                sl_source = кто

        eff_be_offset = rm.eff_points_threshold(cfg.BREAK_EVEN_OFFSET_POINTS, 0.05, atr_value, point)
        eff_trail_min = rm.eff_points_threshold(cfg.TRAILING_MIN_POINTS, 0.3, atr_value, point)
        eff_profit_lock = rm.eff_points_threshold(cfg.PROFIT_LOCK_START_POINTS, 0.15, atr_value, point)
        eff_trail_step = rm.eff_points_threshold(cfg.TRAILING_STEP_MIN_POINTS, 0.02, atr_value, point)

        # Порог Profit Lock не может быть меньше доли ОТ РИСКА САМОЙ СДЕЛКИ
        # (её 1R = исходное расстояние price_open<->SL). Раньше порог считался
        # ТОЛЬКО от ATR, без привязки к тому, насколько узкий стоп у профиля —
        # у "Истерички" (atr_sl_multiplier=0.5) это давало запуск лока уже на
        # ~30% риска, РАНЬШЕ безубытка: сделка фиксировалась в крошечный плюс
        # почти сразу, а неудачная — теряла всю дистанцию стопа целиком (см.
        # PROFIT_LOCK_START_R_FRACTION в config.py и update_position_risk()).
        if risk_points > 0:
            r_fraction = getattr(cfg, "PROFIT_LOCK_START_R_FRACTION", 1.0)
            eff_profit_lock = max(eff_profit_lock, risk_points * r_fraction)

        # 1) Break Even
        be_trigger_pts = (atr_value * cfg.BREAK_EVEN_ATR_MULTIPLIER) / point if point else 0
        if cfg.USE_BREAK_EVEN and profit_points >= be_trigger_pts:
            be_sl = p.price_open + eff_be_offset * point if is_buy else p.price_open - eff_be_offset * point
            предложить(be_sl, ПРИЧИНА_БЕЗУБЫТОК)

        # 2) ATR-трейлинг
        trail_pts = max(eff_trail_min, (atr_value * cfg.TRAILING_ATR_MULTIPLIER) / point if point else 0)
        if cfg.USE_TRAILING_STOP and profit_points >= trail_pts:
            trail_sl = price - trail_pts * point if is_buy else price + trail_pts * point
            предложить(trail_sl, ПРИЧИНА_ТРЕЙЛИНГ)

        # 2.5) Лестница трейлинга в единицах СОБСТВЕННОГО РИСКА сделки (R).
        # Включается сильно раньше безубытка по ATR и отступает от пика на
        # долю R, а не на почти целый стоп — см. r_ladder_lock_points().
        if getattr(cfg, "USE_R_TRAIL_LADDER", False):
            lock_pts = r_ladder_lock_points(
                peak_points, risk_points,
                getattr(cfg, "R_TRAIL_LADDER", None),
                float(getattr(cfg, "R_TRAIL_GIVEBACK_R", 0) or 0))
            if lock_pts is not None:
                ladder_sl = (p.price_open + lock_pts * point) if is_buy \
                    else (p.price_open - lock_pts * point)
                предложить(ladder_sl, ПРИЧИНА_ЛЕСТНИЦА)

        # 3) Profit Lock — гонится за ПИКОВОЙ прибылью, а не текущей ценой.
        # Ступенчатый % (см. _tiered_lock_percent) вместо одного фиксированного —
        # чем выше был пик, тем больше от него запирается стопом.
        if cfg.USE_PROFIT_LOCK_TRAILING and peak_points >= eff_profit_lock:
            lock_pct = (_tiered_lock_percent(peak_points, eff_profit_lock)
                        if getattr(cfg, "USE_TIERED_PROFIT_LOCK", False)
                        else cfg.PROFIT_LOCK_PERCENT)
            lock_points = peak_points * lock_pct / 100.0
            lock_sl = p.price_open + lock_points * point if is_buy else p.price_open - lock_points * point
            предложить(lock_sl, ПРИЧИНА_ЛОК)

        improved = (best_sl > current_sl) if is_buy else (current_sl == 0 or best_sl < current_sl)
        dist_ok = broker_min_dist <= 0 or abs(price - best_sl) >= broker_min_dist
        step_ok = (current_sl == 0
                   or abs(best_sl - current_sl) + point * 1e-6 >= eff_trail_step * point)
        sl_changed = best_sl != current_sl and improved and dist_ok and step_ok
        if not sl_changed:
            best_sl = current_sl
        else:
            # Имя запоминаем ТОЛЬКО когда стоп действительно переехал. Иначе
            # в журнал попал бы механизм, который что-то предложил, но был
            # отвергнут брокерской дистанцией или шагом.
            _position_sl_source[p.ticket] = sl_source

        # 4) Спасение в безубыток: сделка глубоко проседала и вернулась к нулю.
        # Проверяем ДО подтягивания TP — если пора закрывать, TP уже не нужен.
        best_tp = current_tp
        rescued = False
        if getattr(cfg, "USE_BREAK_EVEN_RESCUE", False):
            eff_rescue_dd = rm.eff_points_threshold(
                getattr(cfg, "BE_RESCUE_MIN_DRAWDOWN_POINTS", 20), 0.25, atr_value, point)
            eff_rescue_exit = rm.eff_points_threshold(
                getattr(cfg, "BE_RESCUE_EXIT_POINTS", 3), 0.03, atr_value, point)
            action = break_even_rescue_action(
                trough_points, profit_points, age_seconds,
                eff_rescue_dd, getattr(cfg, "BE_RESCUE_AFTER_MINUTES", 10) * 60.0,
                eff_rescue_exit)

            if action == "close":
                _position_sl_source[p.ticket] = ПРИЧИНА_СПАСЕНИЕ
                log.info("%s тикет %s: спасение в безубыток — просадка была %.1f пт, "
                         "вернулась к %.1f пт, закрываю", symbol, p.ticket, trough_points, profit_points)
                if cfg.LIVE_TRADING:
                    result = mt5c.close_position_partial(p, p.volume)
                    if result is None or result.retcode != mt5c.RETCODE_DONE:
                        log.warning("%s тикет %s: не удалось закрыть в безубыток (%s)",
                                    symbol, p.ticket, result)
                    else:
                        continue          # позиции больше нет — TP ей уже не нужен
                else:
                    log.debug("[DRY-RUN] %s тикет %s: закрытие в безубыток", symbol, p.ticket)
                    continue
            elif action == "arm":
                # Цель прибыли переносится на уровень безубытка — сделка
                # закроется сама, как только рынок вернёт её к нулю, даже если
                # программа в этот момент занята другими символами.
                rescue_tp = tighten_take_profit(
                    is_buy, p.price_open, price, current_tp,
                    eff_rescue_exit, eff_rescue_exit, point, broker_min_dist, 0.0)
                if rescue_tp > 0:
                    best_tp = rescue_tp
                    rescued = True
                    _position_tp_source[p.ticket] = ПРИЧИНА_СПАСЕНИЕ

        # 5) Подтягивание тейк-профита — только ближе к цене, никогда дальше.
        if getattr(cfg, "USE_TP_TIGHTEN", False) and not rescued:
            base_target = learned_tp_points if learned_tp_points > 0 else \
                (atr_value * getattr(cfg, "TP_TIGHTEN_START_ATR", 1.5)) / point if point else 0.0
            target = shrunk_target_points(
                base_target, age_seconds,
                getattr(cfg, "TP_TIGHTEN_SHRINK_PER_MINUTE", 0.10),
                getattr(cfg, "TP_TIGHTEN_MIN_FRACTION", 0.25))
            eff_min_profit = rm.eff_points_threshold(
                getattr(cfg, "TP_TIGHTEN_MIN_PROFIT_POINTS", 10), 0.08, atr_value, point)
            eff_tp_step = rm.eff_points_threshold(
                getattr(cfg, "TP_TIGHTEN_STEP_POINTS", 5), 0.02, atr_value, point)
            new_tp = tighten_take_profit(
                is_buy, p.price_open, price, current_tp, target,
                eff_min_profit, point, broker_min_dist, eff_tp_step,
                risk_points=risk_points,
                min_r=getattr(cfg, "TP_TIGHTEN_MIN_R", 1.0))
            if new_tp > 0:
                best_tp = new_tp
                _position_tp_source[p.ticket] = (
                    "LEARNED_TP" if learned_tp_points > 0 else "TP_TIGHTEN")

        tp_changed = best_tp != current_tp

        if sl_changed or tp_changed:
            if cfg.LIVE_TRADING:
                result = mt5c.modify_position(p.ticket, best_sl, best_tp)
                # Отказ брокера здесь раньше не проверялся вообще. А значит
                # трейлинг мог не работать НИ РАЗУ за сделку — стоп оставался
                # на месте, программа молчала, и понять это было неоткуда.
                # Пишем один раз на сделку: попытка повторяется каждую
                # секунду, и без этого журнал был бы залит.
                if result is None or getattr(result, "retcode", None) != mt5c.RETCODE_DONE:
                    if p.ticket not in _modify_failed_tickets:
                        _modify_failed_tickets.add(p.ticket)
                        log.warning(
                            "%s тикет %s: брокер не принял перенос стопа/цели "
                            "(SL %.5f, TP %.5f) — %s. Трейлинг по этой сделке "
                            "не работает.", symbol, p.ticket, best_sl, best_tp, result)
                        runtime_events.record(
                            "стоп", f"{symbol}: брокер не принял перенос стопа — трейлинг не работает")
                else:
                    _modify_failed_tickets.discard(p.ticket)
            else:
                log.debug("[DRY-RUN] %s тикет %s: SL -> %.5f, TP -> %.5f",
                          symbol, p.ticket, best_sl, best_tp)
