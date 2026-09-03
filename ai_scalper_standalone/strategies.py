"""Готовые торговые стратегии — наборы настроек под разный характер рынка.

Зачем это нужно. В программе больше сотни параметров, и подобрать их
осмысленно вручную почти невозможно. Стратегия — это согласованный набор
значений, который перестраивает поведение целиком: какой сигнал считать
хорошим, как далеко ставить стоп, держать сделку или забирать быстро.

Важно понимать: это НЕ разные торговые роботы и НЕ обещание прибыли.
Движок остаётся тем же — меняются акценты. Ни одна стратегия не отключает
защиты: дневной лимит убытка, ограничение просадки и пауза после серии
убытков работают всегда.

Три семейства взяты не с потолка — это классика, вокруг которой построено
большинство открытых советников:

  * следование тренду — входим по направлению движения, держим дольше;
  * возврат к среднему — входим против перегретого движения, забираем быстро;
  * пробой — ждём выход из накопления и идём за импульсом.

Каждая стратегия описана словами, чтобы было видно, что именно она меняет.
"""

from __future__ import annotations

import json
import logging
import os
import sys

from dataclasses import dataclass, field

log = logging.getLogger("strategies")


@dataclass
class Strategy:
    """Одна стратегия: имя, объяснение и набор параметров."""

    key: str
    title: str
    idea: str                 # одна фраза: в чём смысл
    when: str                 # когда уместна
    caution: str              # чем плоха, честно
    params: dict = field(default_factory=dict)

    def summary(self) -> str:
        return f"{self.title} — {self.idea}"


# Порядок важен: первым идёт то, с чего разумно начинать.
STRATEGIES: list[Strategy] = [

    Strategy(
        key="balanced_hybrid",
        title="Универсальная",
        idea="откат по тренду плюс подтверждение индикаторами",
        when="подходит для начала: работает и в тренде, и в спокойном рынке",
        caution="ничего не делает лучше всех — это середина по всем показателям",
        params={
            # Родная логика программы: откат к EMA + price action
            "USE_SCORE_FILTER": True,
            "USE_MARKET_REGIME_FILTER": True,
            "USE_ADAPTIVE_SCORE_WEIGHTS": True,
            "USE_MULTI_INDICATOR": True,
            "MULTI_INDICATOR_WEIGHT": 10,
            "EMA_FAST_PERIOD": 8,
            "EMA_SLOW_PERIOD": 21,
            "EMA_TREND_PERIOD": 50,
            "ADX_MIN_LEVEL": 20,
            "RSI_OVERBOUGHT": 70,
            "RSI_OVERSOLD": 30,
            "RISK_REWARD_RATIO": 2.0,
            "MIN_RISK_REWARD_RATIO": 1.5,
            "USE_MAX_PROFIT_RIDE": False,
        },
    ),

    Strategy(
        key="trend_follow",
        title="По тренду",
        idea="идём в сторону движения и держим, пока оно живо",
        when="когда рынок явно куда-то идёт: сильные новостные дни, открытие сессий",
        caution="во флэте даёт много ложных входов — их отсекает фильтр режима рынка",
        params={
            "USE_SCORE_FILTER": True,
            "USE_MARKET_REGIME_FILTER": True,     # во флэте вход блокируется
            "USE_ADAPTIVE_SCORE_WEIGHTS": True,
            "USE_MULTI_INDICATOR": True,
            "MULTI_INDICATOR_WEIGHT": 8,
            # Медленнее EMA — меньше дёрганья на шуме
            "EMA_FAST_PERIOD": 12,
            "EMA_SLOW_PERIOD": 34,
            "EMA_TREND_PERIOD": 100,
            "ADX_MIN_LEVEL": 25,                  # требуем выраженный тренд
            "REGIME_ADX_TREND_LEVEL": 25,
            "REGIME_TREND_BONUS": 12,
            "REGIME_RANGE_PENALTY": 20,           # флэт сильно штрафуется
            # Прибыль тянем трейлингом, фиксированного TP нет
            "USE_MAX_PROFIT_RIDE": True,
            "RISK_REWARD_RATIO": 3.0,
            "MIN_RISK_REWARD_RATIO": 2.0,
        },
    ),

    Strategy(
        key="mean_reversion",
        title="Возврат к среднему",
        idea="входим против перегретого движения и забираем быстро",
        when="в спокойном рынке без новостей, когда цена ходит в диапазоне",
        caution="самая опасная в сильном тренде: движение может не вернуться. "
                "Стоп обязателен, размер сделки меньше",
        params={
            "USE_SCORE_FILTER": True,
            "USE_MARKET_REGIME_FILTER": True,
            "USE_ADAPTIVE_SCORE_WEIGHTS": True,
            # Полосы Боллинджера и стохастик — основа этой логики
            "USE_MULTI_INDICATOR": True,
            "MULTI_INDICATOR_WEIGHT": 18,
            "BB_PERIOD": 20,
            "BB_STD_MULT": 2.0,
            "STOCH_K_PERIOD": 14,
            "STOCH_D_PERIOD": 3,
            # Быстрые EMA: важна не сила тренда, а отклонение от середины
            "EMA_FAST_PERIOD": 5,
            "EMA_SLOW_PERIOD": 13,
            "ADX_MIN_LEVEL": 12,                  # сильный тренд здесь ВРЕДЕН
            "REGIME_TREND_BONUS": 0,
            "REGIME_RANGE_PENALTY": 0,            # флэт — родная среда
            "RSI_OVERBOUGHT": 75,                 # ждём настоящей перегретости
            "RSI_OVERSOLD": 25,
            # Забираем быстро: цель близко, тянуть нечего
            "USE_MAX_PROFIT_RIDE": False,
            "RISK_REWARD_RATIO": 1.2,
            "MIN_RISK_REWARD_RATIO": 1.0,
        },
    ),

    Strategy(
        key="breakout",
        title="Пробой",
        idea="ждём выход цены из накопления и идём за импульсом",
        when="перед открытием Лондона и Нью-Йорка, после долгого затишья",
        caution="ложные пробои — обычное дело; помогает фильтр объёма и "
                "требование подтверждающей свечи",
        params={
            "USE_SCORE_FILTER": True,
            "USE_MARKET_REGIME_FILTER": False,    # пробой рождается ИЗ флэта
            "USE_ADAPTIVE_SCORE_WEIGHTS": True,
            "USE_MULTI_INDICATOR": True,
            "MULTI_INDICATOR_WEIGHT": 12,
            "EMA_FAST_PERIOD": 8,
            "EMA_SLOW_PERIOD": 21,
            "EMA_TREND_PERIOD": 50,
            "ADX_MIN_LEVEL": 18,
            # Требуем сильную свечу пробоя: крупное тело, маленькие тени
            "BODY_PERCENT_MIN": 60,
            "MAX_WICK_PERCENT": 30,
            "PULLBACK_TOLERANCE_POINTS": 40,
            # Импульс тянем трейлингом
            "USE_MAX_PROFIT_RIDE": True,
            "RISK_REWARD_RATIO": 2.5,
            "MIN_RISK_REWARD_RATIO": 1.8,
        },
    ),

    Strategy(
        key="careful_scalp",
        title="Осторожный скальп",
        idea="редкие входы только при совпадении всех условий",
        when="когда важнее не потерять, чем заработать: реальный счёт, новый брокер",
        caution="сделок будет мало, иногда ни одной за день — это нормально",
        params={
            "USE_SCORE_FILTER": True,
            "USE_MARKET_REGIME_FILTER": True,
            "USE_ADAPTIVE_SCORE_WEIGHTS": True,
            "USE_MULTI_INDICATOR": True,
            "MULTI_INDICATOR_WEIGHT": 15,
            "EMA_FAST_PERIOD": 8,
            "EMA_SLOW_PERIOD": 21,
            "EMA_TREND_PERIOD": 50,
            "ADX_MIN_LEVEL": 28,                  # только сильное движение
            "REGIME_CONFIRM_BARS": 3,             # режим должен подтвердиться
            "REGIME_RANGE_PENALTY": 25,
            "RSI_OVERBOUGHT": 68,
            "RSI_OVERSOLD": 32,
            "BODY_PERCENT_MIN": 55,
            "MAX_WICK_PERCENT": 35,
            "USE_MAX_PROFIT_RIDE": False,
            "RISK_REWARD_RATIO": 2.5,
            "MIN_RISK_REWARD_RATIO": 2.0,         # плохое соотношение не берём
        },
    ),

    Strategy(
        key="c001_simple",
        title="С-001 черновик",
        idea="сделка ведётся только стопом и целью, программа к ней не лезет",
        when="только для проверки по регламенту: паспорт версии, демо, разбор",
        caution=(
            "прибыльность НЕ проверена и НЕ обещана. Разбор истории показал "
            "лишь, что простой выход ТЕРЯЕТ МЕНЬШЕ сложного (0,09-0,13 R на "
            "сделку), а не что он зарабатывает. Включать в работу без "
            "заверённого паспорта С-001 и демо-приёмки нельзя"),
        params={
            # Ведение сделки выключено целиком. Ровно один смысл: после
            # входа программа не прикасается к позиции, работают стоп и
            # цель у брокера.
            "USE_SIMPLE_EXIT": True,
            "USE_MAX_PROFIT_RIDE": False,
            "USE_BREAK_EVEN": False,
            "USE_TRAILING_STOP": False,
            "USE_PROFIT_LOCK_TRAILING": False,
            "USE_R_TRAIL_LADDER": False,
            "USE_PARTIAL_CLOSE": False,
            # Спасение в безубыток — тоже вмешательство в открытую
            # сделку, и в списке его не было по недосмотру. Указал
            # проверяющий.
            "USE_BREAK_EVEN_RESCUE": False,
            "USE_TP_TIGHTEN": False,
            # Цель равна риску. Это НЕ подбор «под прибыль»: единица
            # выбрана как самое простое из возможных значений, чтобы
            # сравнение со сложным выходом не зависело ещё и от того,
            # насколько далеко поставлена цель.
            "RISK_REWARD_RATIO": 1.0,
        },
    ),

    Strategy(
        key="mirror",
        title="Зеркало",
        idea="вход в СТОРОНУ, ПРОТИВОПОЛОЖНУЮ сигналу: сигнал на покупку — продаём",
        when=(
            "только для проверки замысла владельца: «сигнал приходит на "
            "покупку пусть ставит на продажу». Отдельная стратегия с "
            "отдельным именем, чтобы её было видно в списке"),
        caution=(
            "ПРОВЕРЕНО И НЕ ПОМОГАЕТ. Прогон по паспорту З-001 на 182 тысячах "
            "сделок: зеркало теряет на ВСЕХ трёх срезах — train −0,043 R, "
            "validation −0,079 R, OOS −0,065 R на сделку. На validation оно "
            "даже ХУЖЕ обычного входа. Причина видна из тех же чисел: "
            "издержки съедают 0,051-0,073 R с каждой сделки, а вклад "
            "стороны входа — всего ±0,008 R, и он меняет знак между "
            "срезами, то есть это шум. Дело не в стороне, а в том, что "
            "сделка стоит дороже, чем даёт любая сторона. Стратегия "
            "оставлена в списке по прямой просьбе владельца, чтобы её "
            "было видно, а не как рабочий вариант"),
        params={
            # РОВНО ОДНА настройка. Ни один фильтр, порог, стоп, цель или
            # размер лота не трогается: иначе изменилось бы сразу два
            # условия, и по итогу нельзя было бы сказать, что подействовало.
            "MIRROR_SIGNALS": True,
        },
    ),

]

# Профили, которые НЕ являются рабочими настройками, а только черновиком
# для проверки по регламенту. Программа не должна предлагать их наравне с
# остальными, и уж тем более включать сама.
ЧЕРНОВИКИ = frozenset({"c001_simple", "mirror"})

# Ничем из этого стратегия управлять не может: защиты счёта живут отдельно
# и переключением стратегии не отключаются.
PROTECTED_PARAMS = frozenset({
    "DAILY_LOSS_LIMIT_PERCENT",
    "MAX_DRAWDOWN_PERCENT",
    "MAX_CONSECUTIVE_LOSSES",
    "PAUSE_HOURS_AFTER_LOSS_STREAK",
    "MAX_TOTAL_RISK_PERCENT",
    "RISK_PERCENT",
    "USE_RISK_PERCENT",
    "LOT_SIZE",
    "MAX_OPEN_POSITIONS",
    "MAX_TRADES_PER_DAY",
})


# У какого черновика какой паспорт. Пока паспорта нет — черновик остаётся
# черновиком и кнопкой не применяется.
ПАСПОРТА = {"c001_simple": "strategy_c001.json",
            "mirror": "strategy_mirror.json"}


def _корень_данных() -> str:
    """Где искать папку preregistration.

    В собранной программе PyInstaller распаковывает вложенные файлы во
    временную папку и кладёт путь в sys._MEIPASS; при запуске из
    исходников поднимаемся на уровень выше — в корень репозитория."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def путь_паспорта(strategy_or_key) -> str:
    """Полный путь к паспорту черновика. Пусто — паспорта не предусмотрено."""
    ключ = str(getattr(strategy_or_key, "key", strategy_or_key))
    имя = ПАСПОРТА.get(ключ)
    if not имя:
        return ""
    return os.path.join(_корень_данных(), "preregistration", имя)


def паспорт_заверен(strategy_or_key) -> tuple:
    """(можно_ли_применять, почему_нет).

    Проверяются ТРИ вещи, и каждая по отдельности закрывает кнопку:
    файл на месте, в нём стоит печать, и печать сходится с содержимым.
    Последнее важнее всего: без сверки печати паспорт можно было бы
    отредактировать в блокноте и «разрешить» себе что угодно."""
    путь = путь_паспорта(strategy_or_key)
    if not путь:
        return False, "для этого черновика паспорт не предусмотрен"
    if not os.path.exists(путь):
        return False, f"паспорт не найден: {os.path.basename(путь)}"
    try:
        with open(путь, "r", encoding="utf-8") as f:
            паспорт = json.load(f)
    except (OSError, ValueError) as e:
        return False, f"паспорт не читается ({type(e).__name__})"

    записанная = str(паспорт.get("хеш_паспорта", "")).strip()
    if not записанная:
        return False, "паспорт не заверен: печати нет"

    # Считаем печать тем же способом, что и при заверении.
    import research_manifest
    если_бы = research_manifest.хеш(паспорт)
    if если_бы != записанная:
        return False, ("печать паспорта НЕ СХОДИТСЯ с его содержимым — "
                       "паспорт меняли после заверения")
    return True, ""


def загруженные() -> list:
    """Стратегии, подтянутые из репозитория. Пусто — значит пусто.

    Владелец: «пускай она просто подтягивает… раз в пять часов», чтобы
    новые стратегии не требовали пересборки программы.

    Ошибка здесь НЕ имеет права лишить человека встроенных стратегий:
    нет сети, нет кэша, битый файл — возвращаем пустой список и работаем
    как раньше."""
    try:
        import strategies_feed
        return strategies_feed.из_кэша()
    except Exception:  # noqa: BLE001
        log.debug("Скачанные стратегии не прочитаны", exc_info=True)
        return []


def все() -> list:
    """Встроенные плюс скачанные. Встроенная всегда побеждает по ключу.

    Совпадение ключей отбрасывается ещё в strategies_feed, здесь стоит
    вторая проверка того же: защита, которую снимает одна правка в
    другом файле, — не защита."""
    свои = list(STRATEGIES)
    занято = {s.key for s in свои}
    for s in загруженные():
        if s.key not in занято:
            занято.add(s.key)
            свои.append(s)
    return свои


def by_key(key: str) -> Strategy | None:
    for strategy in все():
        if strategy.key == key:
            return strategy
    return None


def черновик(strategy_or_key) -> bool:
    """Черновик ли это. Принимает и стратегию, и ключ."""
    ключ = getattr(strategy_or_key, "key", strategy_or_key)
    return str(ключ) in ЧЕРНОВИКИ


def рабочие() -> list:
    """Стратегии, которые можно предлагать наравне. Без черновиков."""
    return [s for s in все() if not черновик(s)]


def пронумерованные(включая_черновики: bool = False) -> list[str]:
    """Названия с номерами: «1. Универсальная», «2. По тренду»...

    Владелец: «чтобы был выбор торговой стратегии по каждому. Один, два,
    три, четыре». Номер — это не украшение: по нему стратегию называют
    вслух и в переписке, не заставляя человека переписывать длинное имя."""
    return [f"{i}. {имя}" for i, имя in enumerate(titles(включая_черновики), 1)]


def по_названию_с_номером(строка: str):
    """Найти стратегию по строке вида «3. Возврат к среднему»."""
    текст = str(строка or "")
    if ". " in текст:
        текст = текст.split(". ", 1)[1]
    return by_title(текст)


def titles(включая_черновики: bool = False) -> list[str]:
    """Названия для списка выбора.

    Черновики по умолчанию НЕ показываются. Стратегия в общем списке
    выглядит как готовый вариант: выбрал — применил. С-001 таким не
    является — у неё нет ни заверённого паспорта, ни демо-приёмки, и
    прибыльность её не проверена. Показать её рядом с остальными значит
    предложить владельцу то, что предлагать нельзя."""
    if включая_черновики:
        источник = все()
    else:
        # Черновик с ЗАВЕРЕННЫМ паспортом показывать можно: регламент
        # пройден, и прятать его дальше значило бы мешать работе. Без
        # паспорта — по-прежнему нет.
        источник = [s for s in все()
                    if not черновик(s) or паспорт_заверен(s)[0]]
    return [s.title for s in источник]


def by_title(title: str) -> Strategy | None:
    for strategy in все():
        if strategy.title == title:
            return strategy
    return None


def safe_params(strategy: Strategy) -> dict:
    """Параметры стратегии без тех, что управляют риском.

    Даже если в описании стратегии по ошибке окажется параметр риска, он
    будет отброшен: размер сделки и лимиты убытка задаются профилем риска
    и настройками счёта, а не стратегией.
    """
    return {k: v for k, v in strategy.params.items() if k not in PROTECTED_PARAMS}


def describe(strategy: Strategy) -> str:
    """Многострочное описание для интерфейса."""
    return (f"{strategy.idea}.\n"
            f"Когда уместна: {strategy.when}.\n"
            f"Осторожно: {strategy.caution}.\n"
            f"Меняет параметров: {len(safe_params(strategy))}.")


# ===========================================================================
# СИГНАЛЬНЫЕ ФУНКЦИИ СТРАТЕГИЙ
#
# Раньше стратегия была только набором настроек. Теперь у каждой есть своя
# оценка сигнала: она смотрит на те же посчитанные индикаторы, но по своей
# логике, и добавляет баллы к общему score — так же, как это делают
# custom_strategy.py и AI-сигнал.
#
# Оценка ограничена диапазоном 0..25 и только ДОБАВЛЯЕТ баллы, поэтому
# стратегия не может протолкнуть сделку в обход остальных фильтров: порог
# входа, режим рынка, спред, новости и лимиты риска проверяются как обычно.
# ===========================================================================

SCORE_MAX = 25.0


def _clamp(value: float, lo: float = 0.0, hi: float = SCORE_MAX) -> float:
    return max(lo, min(hi, value))


def _last(df, column: str, default: float = 0.0) -> float:
    """Последнее значение колонки; 0, если колонки нет или данных мало."""
    try:
        if column not in df.columns or len(df) == 0:
            return default
        value = float(df[column].iloc[-1])
        return default if value != value else value  # отсекаем NaN
    except Exception:  # noqa: BLE001
        return default


def score_trend_follow(direction: int, df, atr_value: float) -> float:
    """По тренду: EMA выстроены по направлению, ADX подтверждает силу.

    Чем дальше цена ушла от медленной EMA в сторону сделки и чем выше ADX,
    тем больше баллов. Против направления EMA баллов не даём вовсе.
    """
    ema_fast = _last(df, "ema_fast")
    ema_slow = _last(df, "ema_slow")
    close = _last(df, "close")
    adx_value = _last(df, "adx")
    if not (ema_fast and ema_slow and close and atr_value > 0):
        return 0.0

    aligned = (ema_fast > ema_slow) if direction > 0 else (ema_fast < ema_slow)
    if not aligned:
        return 0.0  # сделка против тренда — эта стратегия её не поддерживает

    beyond = (close - ema_slow) if direction > 0 else (ema_slow - close)
    if beyond <= 0:
        return 0.0

    distance_score = _clamp(beyond / atr_value * 8.0, 0, 14)
    strength_score = _clamp((adx_value - 20.0) * 0.8, 0, 11)
    return _clamp(distance_score + strength_score)


def score_mean_reversion(direction: int, df, atr_value: float) -> float:
    """Возврат к среднему: входим ПРОТИВ перегретого движения.

    Покупка ждёт цену у нижней полосы Боллинджера с перепроданным RSI,
    продажа — у верхней с перекупленным. Сильный тренд гасит оценку:
    в тренде «перегретость» может длиться очень долго.
    """
    close = _last(df, "close")
    bb_mid = _last(df, "bb_mid")
    bb_upper = _last(df, "bb_upper")
    bb_lower = _last(df, "bb_lower")
    rsi_value = _last(df, "rsi", 50.0)
    stoch_k = _last(df, "stoch_k", 50.0)
    adx_value = _last(df, "adx")
    if not (close and bb_mid and bb_upper and bb_lower):
        return 0.0

    half_width = (bb_upper - bb_lower) / 2.0
    if half_width <= 0:
        return 0.0

    # Насколько цена отклонилась от середины в НУЖНУЮ для входа сторону
    if direction > 0:
        deviation = (bb_mid - close) / half_width      # цена ниже середины
        oversold = _clamp((35.0 - rsi_value) * 0.5, 0, 7)
        stoch_extreme = _clamp((25.0 - stoch_k) * 0.2, 0, 4)
    else:
        deviation = (close - bb_mid) / half_width      # цена выше середины
        oversold = _clamp((rsi_value - 65.0) * 0.5, 0, 7)
        stoch_extreme = _clamp((stoch_k - 75.0) * 0.2, 0, 4)

    if deviation <= 0:
        return 0.0

    deviation_score = _clamp(deviation * 14.0, 0, 14)
    raw = deviation_score + oversold + stoch_extreme

    # Сильный тренд — главная опасность этой стратегии: гасим оценку
    if adx_value > 30:
        raw *= 0.4
    elif adx_value > 25:
        raw *= 0.7
    return _clamp(raw)


def score_breakout(direction: int, df, atr_value: float) -> float:
    """Пробой: цена вышла за границу недавнего диапазона сильной свечой.

    Смотрим на максимум/минимум последних баров (без текущего), силу тела
    свечи и расширение диапазона — вялый выход за границу не считается.
    """
    lookback = 20
    try:
        if len(df) < lookback + 2 or atr_value <= 0:
            return 0.0
        window = df.iloc[-(lookback + 1):-1]
        close = float(df["close"].iloc[-1])
        open_ = float(df["open"].iloc[-1]) if "open" in df.columns else close
        high = float(df["high"].iloc[-1])
        low = float(df["low"].iloc[-1])
        prior_high = float(window["high"].max())
        prior_low = float(window["low"].min())
    except Exception:  # noqa: BLE001
        return 0.0

    if direction > 0:
        beyond = close - prior_high
    else:
        beyond = prior_low - close
    if beyond <= 0:
        return 0.0  # границу не пробили

    breakout_score = _clamp(beyond / atr_value * 12.0, 0, 13)

    # Свеча пробоя должна быть уверенной: крупное тело, небольшие тени
    candle_range = high - low
    body = abs(close - open_)
    body_score = _clamp((body / candle_range) * 8.0, 0, 8) if candle_range > 0 else 0.0

    # Расширение диапазона: интерес к движению, а не вялое сползание
    try:
        ranges = (df["high"] - df["low"]).iloc[-(lookback + 1):]
        avg_range = float(ranges.iloc[:-1].mean())
        expansion = _clamp((candle_range / avg_range - 1.0) * 6.0, 0, 4) if avg_range > 0 else 0.0
    except Exception:  # noqa: BLE001
        expansion = 0.0

    return _clamp(breakout_score + body_score + expansion)


def score_careful_scalp(direction: int, df, atr_value: float) -> float:
    """Осторожный скальп: баллы только при совпадении ТРЁХ условий сразу.

    Тренд по EMA, подтверждение MACD и RSI не в зоне разворота. Если хотя бы
    одно не выполнено — ноль. Отсюда и малое число сделок.
    """
    ema_fast = _last(df, "ema_fast")
    ema_slow = _last(df, "ema_slow")
    macd_hist = _last(df, "macd_hist")
    rsi_value = _last(df, "rsi", 50.0)
    adx_value = _last(df, "adx")
    if not (ema_fast and ema_slow):
        return 0.0

    if direction > 0:
        conditions = (ema_fast > ema_slow, macd_hist > 0, 45 <= rsi_value <= 68)
    else:
        conditions = (ema_fast < ema_slow, macd_hist < 0, 32 <= rsi_value <= 55)

    if not all(conditions):
        return 0.0
    return _clamp(10.0 + _clamp((adx_value - 22.0) * 0.9, 0, 15))


def score_balanced_hybrid(direction: int, df, atr_value: float) -> float:
    """Универсальная: своей оценки не добавляет.

    Логика программы уже сбалансирована, дополнительное мнение только
    сместило бы её. Возвращаем 0 — работает штатный скоринг.
    """
    return 0.0


SIGNAL_FUNCTIONS = {
    "balanced_hybrid": score_balanced_hybrid,
    "trend_follow": score_trend_follow,
    "mean_reversion": score_mean_reversion,
    "breakout": score_breakout,
    "careful_scalp": score_careful_scalp,
}


def calc_strategy_score(key: str, direction: int, df, atr_value: float) -> float:
    """Оценка активной стратегии. Неизвестный ключ = 0, без ошибки."""
    fn = SIGNAL_FUNCTIONS.get(key)
    if fn is None:
        return 0.0
    try:
        return _clamp(float(fn(direction, df, atr_value)))
    except Exception:  # noqa: BLE001
        return 0.0  # сбой в стратегии не должен ронять торговый цикл


def apply_strategy_score(score: float, contribution: float, weight: float) -> float:
    """Добавляет вклад стратегии к общему score, не выходя за 0..100.

    Только ДОБАВЛЯЕТ: стратегия не может обнулить сигнал, посчитанный
    остальной логикой, и не может протолкнуть вход в обход фильтров.
    """
    if contribution <= 0 or weight <= 0:
        return score
    bonus = contribution / SCORE_MAX * weight
    return max(0.0, min(100.0, score + bonus))
