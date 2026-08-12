"""
telegram_signals.py — разбор торговых сигналов из Telegram и БЕЗОПАСНОЕ их
применение.

ГЛАВНОЕ ПРАВИЛО ЭТОГО ФАЙЛА
---------------------------
Чужой сигнал не управляет вашими деньгами. Автор сигнала не знает ни вашего
депозита, ни ваших лимитов, ни уже открытых позиций. Поэтому здесь жёстко
зафиксировано, что сигнал МОЖЕТ и чего НЕ МОЖЕТ:

  МОЖЕТ:
    * добавить ограниченное число баллов к оценке (TELEGRAM_MAX_SCORE_BONUS),
      если совпал с направлением, которое программа выбрала САМА;
    * запретить вход, если он противоречит собственному сигналу программы.

  НЕ МОЖЕТ (и не сможет — тут нет соответствующего кода):
    * открыть сделку;
    * увеличить лот или риск;
    * отодвинуть стоп-лосс;
    * отменить дневной лимит убытка или любой другой фильтр.

Это то же правило, что для ИИ-сигнала (см. ai_signal.py) — по требованию
пользователя из исходного задания: «ИИ и любые адаптивные правила никогда не
могут: увеличить риск; расширить уже установленный Stop Loss; отменить
дневной лимит; открыть сделку в обход локальных фильтров».

РЕЖИМЫ (TELEGRAM_ROLE)
----------------------
  "show"  — только показывать в программе. На торговлю не влияет ВООБЩЕ.
            Значение по умолчанию: подключение источника не должно молча
            менять поведение бота.
  "veto"  — плюс к показу: может запретить вход, противоречащий сигналу.
  "score" — плюс к вето: совпавший сигнал добавляет баллы (с потолком).

ПОЧЕМУ ЧИТАТЬ ПРИХОДИТСЯ ПОД ВАШИМ АККАУНТОМ
--------------------------------------------
Telegram запрещает ботам видеть сообщения других ботов ("Bots will not be
able to see messages from other bots regardless of mode" — core.telegram.org/
bots/faq). Поэтому сигналы из чужого бота нельзя прочитать своим ботом: их
читает клиент, вошедший под вашим аккаунтом (см. telegram_reader.py).
"""

import re
from datetime import datetime, timedelta

import config as cfg

ROLE_SHOW = "show"
ROLE_VETO = "veto"
ROLE_SCORE = "score"

BUY = 1
SELL = -1

# Слова направления. Списки намеренно короткие и явные: чем шире эвристика,
# тем выше шанс принять за сигнал обычный текст ("продажи выросли").
_BUY_WORDS = ("buy", "long", "лонг", "покупка", "покупаем", "купить", "🟢")
_SELL_WORDS = ("sell", "short", "шорт", "продажа", "продаём", "продаем", "продать", "🔴")

# Инструменты, которые вообще имеет смысл распознавать. Берём из известных
# программе кодов, чтобы не выдумывать тикеры, которых у брокера нет.
_KNOWN_INSTRUMENTS = (
    "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD",
    "NZDUSD", "USDCAD", "EURJPY", "GBPJPY", "BTCUSD", "ETHUSD", "BTC", "ETH",
    # "ЗОЛОТ" — основа слова, чтобы ловились и "золото", и "золота", и
    # "золотом": в русском тексте инструмент почти всегда в косвенном падеже.
    "GOLD", "ЗОЛОТ",
)

# "GOLD" и "ЗОЛОТО" — это тот же XAUUSD
_ALIASES = {"GOLD": "XAUUSD", "ЗОЛОТ": "XAUUSD", "ЗОЛОТО": "XAUUSD",
            "BTC": "BTCUSD", "ETH": "ETHUSD"}


def role() -> str:
    r = str(getattr(cfg, "TELEGRAM_ROLE", ROLE_SHOW)).lower()
    return r if r in (ROLE_SHOW, ROLE_VETO, ROLE_SCORE) else ROLE_SHOW


def enabled() -> bool:
    return bool(getattr(cfg, "TELEGRAM_ENABLED", False))


def signal_ttl_minutes() -> int:
    return int(getattr(cfg, "TELEGRAM_SIGNAL_TTL_MIN", 30))


def max_score_bonus() -> float:
    """Потолок вклада чужого сигнала в оценку.

    Ограничен сверху жёстко, а не только настройкой: даже если кто-то впишет
    в config.py 500, сигнал не сможет в одиночку протолкнуть сделку через
    порог входа."""
    raw = float(getattr(cfg, "TELEGRAM_MAX_SCORE_BONUS", 10.0))
    return max(0.0, min(15.0, raw))


# =====================================================================
# РАЗБОР ТЕКСТА
# =====================================================================
def parse_direction(text: str) -> int:
    """1 = покупка, -1 = продажа, 0 = не понял.

    Если в тексте есть слова обеих сторон — возвращаем 0. Это не
    перестраховка ради перестраховки: в сообщениях вида «закрываем buy,
    открываем sell» угадывание даёт ровно противоположный смысл."""
    low = text.lower()
    has_buy = any(w in low for w in _BUY_WORDS)
    has_sell = any(w in low for w in _SELL_WORDS)
    if has_buy == has_sell:
        return 0
    return BUY if has_buy else SELL


def parse_instrument(text: str) -> str:
    """Первый распознанный инструмент или "" — приведённый к виду XAUUSD."""
    up = text.upper()
    best = ""
    best_pos = len(up) + 1
    for name in _KNOWN_INSTRUMENTS:
        pos = up.find(name)
        if pos >= 0 and pos < best_pos:
            best, best_pos = name, pos
    return _ALIASES.get(best, best)


def parse_signal(text: str, received: datetime = None) -> dict:
    """Сообщение -> сигнал или None, если это не сигнал.

    Возвращает {"instrument", "direction", "text", "time"}.

    Намеренно НЕ разбираем предлагаемые вход/стоп/цель: свои уровни программа
    считает сама, от собственного ATR и своего риска. Брать их из чужого
    сообщения — значит впустить чужие цифры прямо в управление риском."""
    if not text:
        return None
    instrument = parse_instrument(text)
    if not instrument:
        return None
    direction = parse_direction(text)
    if direction == 0:
        return None
    return {
        "instrument": instrument,
        "direction": direction,
        "text": text.strip()[:200],
        "time": received or datetime.now(),
    }


def symbol_matches(symbol: str, instrument: str) -> bool:
    """Учитывает суффиксы брокера: XAUUSDs, EURUSD.a, BTCUSDm — всё это те же
    инструменты."""
    if not instrument:
        return False
    clean = re.sub(r"[^A-Z]", "", symbol.upper())
    return clean.startswith(instrument)


# =====================================================================
# ХРАНИЛИЩЕ ПОСЛЕДНИХ СИГНАЛОВ
# =====================================================================
# Живёт в памяти процесса: сигнал старше TTL всё равно бесполезен, а
# сохранять чужие торговые рекомендации на диск нет причин.
_signals: dict = {}       # instrument -> сигнал
_history: list = []       # последние сообщения для показа в программе
_HISTORY_LIMIT = 50
_status: dict = {"connected": False, "detail": "не подключено", "last_message": None}


def remember(signal: dict) -> None:
    if not signal:
        return
    _signals[signal["instrument"]] = signal
    _history.append(signal)
    while len(_history) > _HISTORY_LIMIT:
        _history.pop(0)
    _status["last_message"] = signal["time"]


def history() -> list:
    return list(reversed(_history))


def set_status(connected: bool, detail: str) -> None:
    _status["connected"] = connected
    _status["detail"] = detail


def status() -> dict:
    return dict(_status)


def clear() -> None:
    _signals.clear()
    _history.clear()


def signal_for(symbol: str, now: datetime = None) -> dict:
    """Свежий сигнал по этому символу или None.

    Протухший сигнал НЕ применяется: рекомендация часовой давности к текущему
    рынку отношения уже не имеет, а вести себя так, будто имеет, — хуже, чем
    не иметь сигнала вовсе."""
    if not enabled():
        return None
    now = now or datetime.now()
    ttl = timedelta(minutes=signal_ttl_minutes())
    for instrument, sig in _signals.items():
        if not symbol_matches(symbol, instrument):
            continue
        if now - sig["time"] > ttl:
            continue
        return sig
    return None


# =====================================================================
# ПРИМЕНЕНИЕ — только ограничение и ограниченная надбавка
# =====================================================================
def veto_entry(symbol: str, direction: int, now: datetime = None) -> bool:
    """True = вход запрещён, потому что свежий сигнал говорит в другую сторону.

    Отсутствие сигнала запретом НЕ является: молчание источника не должно
    останавливать торговлю."""
    if role() not in (ROLE_VETO, ROLE_SCORE):
        return False
    sig = signal_for(symbol, now)
    if sig is None:
        return False
    return sig["direction"] != direction


def score_bonus(symbol: str, direction: int, now: datetime = None) -> float:
    """Надбавка к оценке за совпадение с чужим сигналом. Всегда >= 0.

    Отрицательной надбавки тут нет намеренно: «наказание» за несовпадение —
    это уже veto_entry, и делать то же самое двумя разными способами значит
    получить двойной эффект, которого никто не заказывал."""
    if role() != ROLE_SCORE:
        return 0.0
    sig = signal_for(symbol, now)
    if sig is None:
        return 0.0
    return max_score_bonus() if sig["direction"] == direction else 0.0


def describe(symbol: str, now: datetime = None) -> str:
    """Короткая строка для интерфейса: что сейчас говорит источник."""
    if not enabled():
        return "выкл"
    sig = signal_for(symbol, now)
    if sig is None:
        return "нет свежего сигнала"
    side = "покупка" if sig["direction"] == BUY else "продажа"
    return f"{side} ({sig['time'].strftime('%H:%M')})"
