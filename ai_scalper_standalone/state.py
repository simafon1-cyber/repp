"""
state.py — рантайм-состояние программы.

В отличие от MQL5-версии (один EA = один график = одни глобальные переменные),
здесь ОДИН процесс ведёт НЕСКОЛЬКО символов сразу, поэтому состояние разделено:

  AccountState  — ОДНО на весь счёт (equity/просадка/дневной лимит — общие для
                  всех символов, т.к. считаются от одного и того же баланса).
  SymbolState   — своё на КАЖДЫЙ символ (бары, режим рынка, анти-дребезг,
                  серия убытков, кэш AI-сигнала) — разные пары живут независимо.
  position peak-profit (для Profit Lock) — общий словарь по тикету позиции,
                  тикеты уникальны в рамках счёта, поэтому символ не нужен.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AccountState:
    day_start_equity: float = 0.0
    peak_equity: float = 0.0
    last_trade_day: datetime = None
    trades_today: int = 0

    total_trades: int = 0
    win_trades: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0


@dataclass
class SymbolState:
    symbol: str

    last_bar_time: datetime = None
    bar_counter: int = 0

    last_close_direction: int = 0   # 1 = закрылась BUY, -1 = закрылась SELL, 0 = сделок ещё не было
    last_close_bar_index: int = -1000

    consecutive_losses: int = 0
    pause_until: datetime = None

    # Режим рынка (MarketRegime)
    current_regime: str = "unknown"   # "unknown" / "trend" / "range"
    regime_candidate: str = "unknown"
    regime_candidate_streak: int = 0

    # Внешний AI-сигнал: кэш последнего успешного ответа
    ext_last_direction: str = ""
    ext_last_confidence: float = 0.0
    ext_last_ok: bool = False
    ext_last_fetch: datetime = None

    last_reject_reason: str = "—"
    last_trade_result: str = "—"
    last_buy_score: float = 0.0
    last_sell_score: float = 0.0

    # Последний посчитанный ATR (в цене, не в пунктах) — кэш для быстрого
    # межопросного мониторинга открытых позиций (см. _fast_position_monitor()
    # в main.py), чтобы не запрашивать заново бары у MT5 только ради ATR.
    last_atr_value: float = 0.0

    # Собственная стратегия программы (custom_strategy.py) — последний
    # посчитанный score по направлению с БОЛЬШИМ основным score, только для
    # отображения в интерфейсе (вкладка "Символы").
    last_custom_score: float = 0.0

    # Доп. подтверждение классическими индикаторами (multi_indicator.py:
    # MACD/Bollinger/Stochastic) — тоже только для отображения.
    last_multi_indicator_score: float = 0.0

    # Автообучение (auto_learning.py): скользящее окно результатов последних
    # сделок по ЭТОМУ символу — True = сделка в плюс, False = в минус.
    # Используется, чтобы самому подстраивать вес AI-сигнала и порог входа.
    recent_results: list = field(default_factory=list)

    # Автообучение целей прибыли: пиковая прибыль (в пунктах) каждой из
    # последних закрытых сделок по этому символу — сколько сделка РЕАЛЬНО
    # проходила в плюс, прежде чем развернуться. По медиане этих значений
    # бот сам подбирает, куда ставить тейк-профит (см. learned_profit_points
    # в auto_learning.py). Пик берётся из trade_manager._closed_peaks.
    recent_peaks: list = field(default_factory=list)

    # Последние ~60 цен закрытия — только для мини-графика цены в desktop-
    # приложении (вкладка "Символы" → окно графика). Никак не влияет на торговлю.
    recent_closes: list = field(default_factory=list)


def pause_active(until: datetime) -> bool:
    if until is None:
        return False
    return datetime.now() < until
