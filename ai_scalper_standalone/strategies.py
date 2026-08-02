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

from dataclasses import dataclass, field


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
]

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


def by_key(key: str) -> Strategy | None:
    for strategy in STRATEGIES:
        if strategy.key == key:
            return strategy
    return None


def titles() -> list[str]:
    return [s.title for s in STRATEGIES]


def by_title(title: str) -> Strategy | None:
    for strategy in STRATEGIES:
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
