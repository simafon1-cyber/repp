"""
desktop_app.py — настольное Windows-приложение поверх торгового движка (main.py
и всё, что он использует). Это ТОЧКА ВХОДА для .exe/установщика — см. BUILD.md.

Всё — счёт, брокер, символы, сделки, лог, график equity, настройки, новости,
чат с Claude — открывается ВНУТРИ этого окна (вкладки), браузер для управления
с компьютера не нужен. Веб-дашборд (Flask) продолжает работать в фоне только
ради доступа С ТЕЛЕФОНА по Wi-Fi — на самом компьютере им пользоваться не
обязательно.

Два режима интерфейса (переключаются на вкладке "Обзор", см. UI_MODE в config.py):
  - Простой     — Обзор / Символы / Сделки / Лог / Настройка / Как пользоваться.
  - Продвинутый — плюс Брокер / Equity / Новости / Chat AI.
    Вкладка "Настройка" ВСЕГДА видна (в обоих режимах — чтобы точно не потерялась):
    быстрые переключатели (профиль/режим/пауза/звук) + ПОЛНЫЙ набор
    input-параметров торговой логики (как у MQL5-советника): индикаторы,
    риск/лот, TP/SL, BE/трейлинг/Profit Lock/частичное закрытие, защитные
    фильтры, режим рынка, контекст, AI, новости, автообучение — редактируется
    прямо в интерфейсе, без открытия config.py руками.
    Вкладка "Как пользоваться" — подробная инструкция по всем вкладкам, тоже
    всегда видна.

ВАЖНО про архитектуру:
  1) Собранный PyInstaller-ом .exe уже содержит весь Python-рантайм внутри —
     отдельного "python.exe" на машине пользователя может не быть. Поэтому
     торговый цикл (main.main()) запускается в ФОНОВОМ ПОТОКЕ этого же
     процесса, а не отдельным подпроцессом. Кнопка "Стоп" останавливает его
     через threading.Event (main.py проверяет его каждую итерацию).
  2) config.py при сборке НЕ встраивается внутрь .exe (см. build_exe.bat:
     --exclude-module config) — он остаётся отдельным редактируемым файлом
     рядом с программой. Поэтому первым делом (до импорта любых внутренних
     модулей проекта) мы добавляем папку с .exe в sys.path.
  3) Веб-дашборд (Flask) поднимается ОДИН РАЗ при старте приложения — иначе
     повторный запуск бота попытался бы занять уже занятый порт 5000.
  4) Вкладки читают то же самое состояние (dashboard_state.get_snapshot(),
     control.py), что и веб-дашборд — никакого HTTP не нужно, всё в одном процессе.
  5) MT5 трогает ТОЛЬКО поток бота (main.main()) и, отдельно, кнопка "Проверить
     подключение" на вкладке "Брокер" (но она заблокирована, пока бот работает,
     чтобы не дёргать API из двух потоков одновременно).
"""

import sys
import os

if getattr(sys, "frozen", False):
    _app_dir = os.path.dirname(sys.executable)
else:
    _app_dir = os.path.dirname(os.path.abspath(__file__))
if _app_dir not in sys.path:
    sys.path.insert(0, _app_dir)
os.chdir(_app_dir)

import base64
import csv
import importlib
import logging
import re
import threading
import webbrowser
import tkinter as tk
from tkinter import messagebox, simpledialog, filedialog, ttk

import config as cfg

LOG_FILE = os.path.join(_app_dir, "scalper.log")
logging.basicConfig(
    level=getattr(logging, cfg.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("desktop_app")

import main as bot_engine
import dashboard_state as ds
import news_calendar
import news_providers
import secure_store
import safe_files
from control import control

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False
    log.info("pystray/Pillow не установлены — работаю без значка в трее.")

APP_TITLE = "AI Scalper Pro"

PROFILE_OPTIONS = [
    ("Консервативный", "conservative"),
    ("Сбалансированный", "balanced"),
    ("Агрессивный", "aggressive"),
    ("Истеричка (YOLO)", "hysteric"),
]
MODE_OPTIONS = [
    ("Скальпинг", "scalping"),
    ("Новости", "news_trading"),
    ("Оба", "both"),
]
ADVANCED_TAB_NAMES = ["Брокер", "Equity", "Новости", "Chat AI"]
# "Настройка" (все настройки + input-параметры) и "Как пользоваться" видны ВСЕГДА,
# в обоих режимах интерфейса — чтобы их точно не потерять в простом режиме.

# ---- Вкладка "Параметры" (продвинутый режим) -------------------------------
# Полный список "входных параметров" торговой логики — как input-параметры
# MQL5-советника: любой из них можно выставить вручную, без правки config.py
# руками. (key, тип, группа, подпись, варианты_для_choice).
# тип: "int" | "float" | "bool" | "choice"
ADVANCED_PARAMS = [
    ("POLL_SECONDS", "int", "Общее", "Частота опроса рынка, сек", None),
    ("TIMEFRAME", "choice", "Общее", "Рабочий таймфрейм", ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]),
    ("TREND_TIMEFRAME", "choice", "Общее", "Старший ТФ тренда (должен быть старше рабочего)",
     ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]),
    ("MAGIC_NUMBER", "int", "Общее", "Magic number (НЕ менять во время работы бота!)", None),
    ("LOG_LEVEL", "choice", "Общее", "Уровень логирования", ["DEBUG", "INFO", "WARNING", "ERROR"]),

    ("EMA_FAST_PERIOD", "int", "Индикаторы", "EMA быстрая — период", None),
    ("EMA_SLOW_PERIOD", "int", "Индикаторы", "EMA медленная — период", None),
    ("EMA_TREND_PERIOD", "int", "Индикаторы", "EMA тренда (старший ТФ) — период", None),
    ("ADX_PERIOD", "int", "Индикаторы", "ADX — период", None),
    ("RSI_PERIOD", "int", "Индикаторы", "RSI — период", None),
    ("ATR_PERIOD", "int", "Индикаторы", "ATR — период", None),
    ("ATR_AVG_PERIOD", "int", "Индикаторы", "ATR — период среднего", None),
    ("ADX_MIN_LEVEL", "int", "Индикаторы", "ADX — минимальный уровень тренда", None),
    ("RSI_OVERBOUGHT", "int", "Индикаторы", "RSI — уровень перекупленности", None),
    ("RSI_OVERSOLD", "int", "Индикаторы", "RSI — уровень перепроданности", None),

    ("PULLBACK_TOLERANCE_POINTS", "int", "Price Action / откат", "Допуск отката, пункты", None),
    ("BODY_PERCENT_MIN", "int", "Price Action / откат", "Мин. % тела свечи", None),
    ("MAX_WICK_PERCENT", "int", "Price Action / откат", "Макс. % тени свечи", None),

    ("USE_VOLUME_FILTER", "bool", "Объём", "Учитывать тиковый объём", None),
    ("VOLUME_AVG_PERIOD", "int", "Объём", "Период среднего объёма", None),

    ("AUTO_ADAPT_TO_SYMBOL", "bool", "Автонастройка под инструмент",
     "Авто-масштабирование порогов под ATR инструмента", None),

    ("MIN_BARS_BETWEEN_REVERSAL", "int", "Защитные проверки", "Анти-дребезг, баров между разворотами", None),
    ("USE_SPREAD_FILTER", "bool", "Защитные проверки", "Фильтр по спреду", None),
    ("MAX_SPREAD_POINTS", "int", "Защитные проверки", "Макс. спред, пункты", None),
    ("USE_VOLATILITY_SPIKE_GUARD", "bool", "Защитные проверки", "Защита от скачков волатильности", None),
    ("VOLATILITY_SPIKE_MULTIPLIER", "float", "Защитные проверки", "Множитель скачка волатильности", None),
    ("USE_ROLLOVER_GUARD", "bool", "Защитные проверки", "Пауза на роллoвер", None),
    ("ROLLOVER_HOUR_SERVER", "int", "Защитные проверки", "Час роллoвера (серверное время)", None),
    ("ROLLOVER_GUARD_MINUTES", "int", "Защитные проверки", "Длительность паузы роллoвера, мин", None),

    ("USE_AI_SIGNAL", "bool", "AI-сигнал", "Использовать внешний AI-сигнал", None),
    ("AI_PROVIDER", "choice", "AI-сигнал", "Провайдер AI", ["claude", "openai"]),
    ("AI_SIGNAL_WEIGHT", "int", "AI-сигнал", "Вес AI-сигнала в score", None),
    ("AI_SIGNAL_CACHE_SECONDS", "int", "AI-сигнал", "Кэш ответа AI, сек", None),
    ("AI_SIGNAL_REQUIRE_DIRECTION", "bool", "AI-сигнал", "Требовать совпадения направления с AI", None),

    ("USE_CUSTOM_STRATEGY", "bool", "Собственная стратегия",
     "Использовать собственную стратегию программы (custom_strategy.py)", None),
    ("CUSTOM_STRATEGY_WEIGHT", "int", "Собственная стратегия", "Вес собственной стратегии в score", None),

    ("USE_MULTI_INDICATOR", "bool", "Доп. индикаторы",
     "Подмешивать MACD/Bollinger/Stochastic в score (multi_indicator.py)", None),
    ("MULTI_INDICATOR_WEIGHT", "int", "Доп. индикаторы", "Вес группы индикаторов в score", None),
    ("MACD_FAST_PERIOD", "int", "Доп. индикаторы", "MACD: период быстрой EMA", None),
    ("MACD_SLOW_PERIOD", "int", "Доп. индикаторы", "MACD: период медленной EMA", None),
    ("MACD_SIGNAL_PERIOD", "int", "Доп. индикаторы", "MACD: период сигнальной линии", None),
    ("BB_PERIOD", "int", "Доп. индикаторы", "Bollinger Bands: период SMA", None),
    ("BB_STD_MULT", "float", "Доп. индикаторы", "Bollinger Bands: множитель стандартного отклонения", None),
    ("STOCH_K_PERIOD", "int", "Доп. индикаторы", "Stochastic: период %K", None),
    ("STOCH_D_PERIOD", "int", "Доп. индикаторы", "Stochastic: период %D", None),

    ("USE_MARKET_REGIME_FILTER", "bool", "Режим рынка", "Учитывать режим рынка (тренд/флэт)", None),
    ("USE_ADAPTIVE_SCORE_WEIGHTS", "bool", "Режим рынка",
     "Умнее веса score: усиливать Тренд/Откат в тренде, RSI во флэте", None),
    ("REGIME_ER_PERIOD", "int", "Режим рынка", "Efficiency Ratio — период", None),
    ("REGIME_ADX_TREND_LEVEL", "int", "Режим рынка", "ADX — порог тренда", None),
    ("REGIME_ADX_RANGE_LEVEL", "int", "Режим рынка", "ADX — порог флэта", None),
    ("REGIME_ER_TREND_LEVEL", "float", "Режим рынка", "ER — порог тренда", None),
    ("REGIME_ER_RANGE_LEVEL", "float", "Режим рынка", "ER — порог флэта", None),
    ("REGIME_CONFIRM_BARS", "int", "Режим рынка", "Баров подтверждения смены режима", None),
    ("REGIME_RANGE_PENALTY", "int", "Режим рынка", "Штраф score во флэте", None),
    ("REGIME_TREND_BONUS", "int", "Режим рынка", "Бонус score в тренде", None),

    ("USE_PA_HARD_GATE", "bool", "Анти-'зеркало' фильтры",
     "Обязательное подтверждение свечи (иначе score=0) — против входа на развороте", None),
    ("BLOCK_ENTRY_IN_RANGE", "bool", "Анти-'зеркало' фильтры",
     "Полный блок входа во флэте (не просто штраф)", None),
    ("USE_EXHAUSTION_FILTER", "bool", "Анти-'зеркало' фильтры",
     "Блокировать вход на уже растянутой (перегретой) свече", None),
    ("EXHAUSTION_RANGE_ATR_RATIO", "float", "Анти-'зеркало' фильтры",
     "Порог: диапазон свечи > этого × средний ATR -> блок входа", None),

    ("USE_MARKET_CONTEXT", "bool", "Контекст рынка", "Учитывать коррелирующие инструменты", None),
    ("CONTEXT_EMA_PERIOD", "int", "Контекст рынка", "EMA контекста — период", None),
    ("CONTEXT_SCORE_WEIGHT", "int", "Контекст рынка", "Вес контекста в score", None),

    ("USE_SCORE_FILTER", "bool", "Score-фильтр", "Входить только по score-порогу профиля", None),

    ("USE_MAX_PROFIT_RIDE", "bool", "Стопы / TP",
     "Без фикс. TP — тянуть максимум прибыли трейлингом/Profit Lock (пока не выбьет стоп)", None),
    ("RISK_REWARD_RATIO", "float", "Стопы / TP", "Risk/Reward (если TP не в деньгах; не используется при макс. профите)", None),
    ("MIN_RISK_REWARD_RATIO", "float", "Стопы / TP",
     "КРИТИЧНО: мин. Risk/Reward — TP никогда не меньше SL x это число, даже если денежная цель профиля скромнее", None),
    ("TP_MIN_POINTS", "int", "Стопы / TP", "Мин. TP, пункты (не используется при макс. профите)", None),
    ("TP_MAX_POINTS", "int", "Стопы / TP", "Макс. TP, пункты (не используется при макс. профите)", None),

    ("USE_BREAK_EVEN", "bool", "Break Even", "Переносить в безубыток", None),
    ("BREAK_EVEN_ATR_MULTIPLIER", "float", "Break Even", "Триггер BE, множитель ATR", None),
    ("BREAK_EVEN_OFFSET_POINTS", "int", "Break Even", "Отступ BE, пункты", None),

    ("USE_TRAILING_STOP", "bool", "Трейлинг-стоп", "Трейлинг-стоп по ATR", None),
    ("TRAILING_ATR_MULTIPLIER", "float", "Трейлинг-стоп", "Множитель ATR трейлинга", None),
    ("TRAILING_MIN_POINTS", "int", "Трейлинг-стоп", "Мин. дистанция трейлинга, пункты", None),
    ("TRAILING_STEP_MIN_POINTS", "int", "Трейлинг-стоп", "Мин. шаг трейлинга, пункты", None),

    ("USE_PROFIT_LOCK_TRAILING", "bool", "Profit Lock", "Фиксация части пиковой прибыли", None),
    ("PROFIT_LOCK_START_POINTS", "int", "Profit Lock", "Старт Profit Lock, пункты (ATR-порог)", None),
    ("PROFIT_LOCK_START_R_FRACTION", "float", "Profit Lock",
     "КРИТИЧНО: лок не стартует, пока прибыль не достигнет этой доли риска сделки (1.0 = не раньше 1R)", None),
    ("PROFIT_LOCK_PERCENT", "int", "Profit Lock",
     "% пиковой прибыли фиксировать (запасной вариант, если ступенчатая фиксация ниже выключена)", None),
    ("USE_TIERED_PROFIT_LOCK", "bool", "Profit Lock",
     "Ступенчатая фиксация: чем выше пик прибыли, тем больший % запирается (уровни — в config.py)", None),
    ("POSITION_MONITOR_SECONDS", "float", "Profit Lock",
     "Как часто (сек) проверять УЖЕ открытые позиции между полными проходами по всем парам", None),

    ("USE_DAILY_LOSS_LIMIT", "bool", "Просадка / серии убытков", "Дневной лимит убытка", None),
    ("USE_MAX_DRAWDOWN_LIMIT", "bool", "Просадка / серии убытков", "Лимит общей просадки", None),
    ("MAX_CONSECUTIVE_LOSSES", "int", "Просадка / серии убытков", "Серия убытков подряд до паузы", None),
    ("PAUSE_HOURS_AFTER_LOSS_STREAK", "int", "Просадка / серии убытков", "Пауза после серии убытков, часы", None),
    ("USE_LOSS_STREAK_RISK_SCALING", "bool", "Просадка / серии убытков", "Снижать риск при серии убытков", None),
    ("MIN_LOSS_STREAK_RISK_MULTIPLIER", "float", "Просадка / серии убытков",
     "Мин. множитель риска при серии убытков", None),

    ("MAX_SPREAD_COST_PERCENT_OF_TP", "float", "Издержки", "Макс. % TP, съедаемый спредом", None),
    ("ORDER_RETRY_ATTEMPTS", "int", "Издержки", "Повторов отправки ордера", None),

    ("NEWS_BREAKOUT_WINDOW_MIN", "int", "Новости (пороги)", "Окно реакции на новость, мин", None),
    ("NEWS_BREAKOUT_MIN_BODY_PCT", "int", "Новости (пороги)", "Мин. % тела свечи для пробоя", None),
    ("NEWS_VOLATILITY_SL_BOOST", "float", "Новости (пороги)", "Множитель SL на новостях", None),

    ("USE_AUTO_LEARNING", "bool", "Автообучение", "Адаптировать вес AI / порог по винрейту", None),
    ("AUTO_LEARNING_WINDOW", "int", "Автообучение", "Окно последних сделок по символу", None),
    ("AUTO_LEARNING_MIN_TRADES", "int", "Автообучение", "Мин. сделок для начала адаптации", None),
    ("AI_WEIGHT_MULT_MIN", "float", "Автообучение", "Мин. множитель веса AI", None),
    ("AI_WEIGHT_MULT_MAX", "float", "Автообучение", "Макс. множитель веса AI", None),
    ("SCORE_THRESHOLD_ADJUST_MIN", "int", "Автообучение", "Мин. коррекция порога score", None),
    ("SCORE_THRESHOLD_ADJUST_MAX", "int", "Автообучение", "Макс. коррекция порога score", None),

    ("USE_CONFIG_HOT_RELOAD", "bool", "Автообновление", "Подхватывать правки config.py на лету", None),
    ("CONFIG_RELOAD_CHECK_SECONDS", "int", "Автообновление", "Период проверки config.py, сек", None),
    ("USE_AUTO_RECONNECT", "bool", "Автообновление", "Автопереподключение к MT5", None),
    ("RECONNECT_AFTER_FAILURES", "int", "Автообновление", "Неудач подряд до переподключения", None),
    ("HISTORY_SYNC_DAYS", "int", "Автообновление", "Синхр. с MetaTrader: глубина истории, дней", None),
    ("HISTORY_SYNC_SECONDS", "int", "Автообновление", "Синхр. с MetaTrader: период обновления, сек", None),

    ("USE_TRADING_HOURS", "bool", "Часы торговли", "Ограничить торговлю часами (иначе круглосуточно)", None),
    ("TRADING_START_HOUR", "int", "Часы торговли", "Час начала торговли, 0-23 (время сервера)", None),
    ("TRADING_END_HOUR", "int", "Часы торговли", "Час окончания торговли, 0-23 (время сервера)", None),

    ("USE_RISK_BASED_LOT", "bool", "Лот", "Считать лот по риску в % от эквити (иначе фиксированный)", None),
    ("LOT_FALLBACK", "float", "Лот", "Фиксированный / запасной лот", None),

    ("NEWS_HARD_BLOCK_WINDOW_MIN", "int", "Новости (пороги)", "Жёсткий блок входа рядом с HIGH-новостью, мин", None),
    ("NEWS_SOFT_PENALTY_POINTS", "float", "Новости (пороги)", "Штраф score рядом с MODERATE-новостью, баллы", None),

    ("AI_SIGNAL_TIMEOUT_MS", "int", "AI-сигнал", "Таймаут запроса к AI, мс", None),

    ("USE_PARTIAL_CLOSE", "bool", "Частичное закрытие", "Частично закрывать позицию при достижении профита", None),
    ("PARTIAL_CLOSE_TRIGGER_POINTS", "int", "Частичное закрытие", "Профит для частичного закрытия, пункты", None),
    ("PARTIAL_CLOSE_PERCENT", "int", "Частичное закрытие", "% объёма закрывать частично", None),

    ("LOG_CSV_PATH", "str", "Общее", "Имя CSV-файла журнала сделок", None),
]

RISK_PROFILE_FIELD_DEFS = [
    ("name", "str", "Название профиля"),
    ("risk_percent", "float", "Риск на сделку, % от эквити"),
    ("atr_sl_multiplier", "float", "Множитель ATR для SL"),
    ("use_money_tp", "bool", "TP в деньгах (иначе Risk/Reward)"),
    ("target_profit_money", "float", "Целевой TP, деньги"),
    ("min_score_to_trade", "int", "Порог score для входа"),
    ("max_open_positions", "int", "Макс. одновременных сделок"),
    ("max_trades_per_day", "int", "Макс. сделок в день"),
    ("daily_loss_limit_pct", "float", "Дневной лимит убытка, %"),
    ("max_drawdown_pct", "float", "Лимит просадки, %"),
    ("max_total_risk_pct", "float", "Лимит совокупного риска, %"),
    ("ignore_soft_filters", "bool", "Игнорировать мягкие фильтры"),
    ("hedge_both_directions", "bool", "Хедж: при сигнале открывать сразу BUY и SELL (обычный SL на каждой ноге)"),
]


def _write_config_value(key: str, value_literal: str):
    """Переписывает ОДНУ строку присваивания в config.py, не трогая остальной
    файл. Запись атомарная (временный файл + os.replace) с проверкой
    синтаксиса ПЕРЕД заменой оригинала и резервной копией — сбой посреди
    записи (антивирус/отключение питания) не испортит config.py (см.
    safe_files.py). После записи доступ к файлу ограничивается текущим
    пользователем Windows."""
    config_path = os.path.join(_app_dir, "config.py")
    with open(config_path, "r", encoding="utf-8") as f:
        text = f.read()
    pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
    new_line = f"{key} = {value_literal}"
    if pattern.search(text):
        text = pattern.sub(new_line, text, count=1)
    else:
        text = text.rstrip("\n") + f"\n{new_line}\n"
    safe_files.atomic_write_text(config_path, text, validate=safe_files.validate_python_syntax)
    safe_files.restrict_to_current_user(config_path)


def _write_config_block(key: str, new_value_literal: str):
    """Как _write_config_value, но для МНОГОСТРОЧНЫХ литералов (RISK_PROFILES,
    MARKET_CONTEXT — словари, которые в config.py форматированы на много строк
    ради читаемости). _write_config_value не годится для них — её regex
    заменяет только ПЕРВУЮ строку присваивания, оставляя "хвост" старого
    многострочного литерала висеть ниже как мусор. Эта функция вместо этого
    находит `key = ...` и определяет КОНЕЦ значения по балансу скобок (с
    учётом строк в кавычках, чтобы не сбиться на скобке внутри текста)."""
    config_path = os.path.join(_app_dir, "config.py")
    with open(config_path, "r", encoding="utf-8") as f:
        text = f.read()

    pattern = re.compile(rf"^{re.escape(key)}\s*=\s*", re.MULTILINE)
    m = pattern.search(text)
    if not m:
        new_text = text.rstrip("\n") + f"\n{key} = {new_value_literal}\n"
        safe_files.atomic_write_text(config_path, new_text, validate=safe_files.validate_python_syntax)
        safe_files.restrict_to_current_user(config_path)
        return

    start = m.end()
    depth = 0
    in_str = None
    started = False
    i = start
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "\"'":
            in_str = ch
        elif ch in "([{":
            depth += 1
            started = True
        elif ch in ")]}":
            depth -= 1
            if started and depth == 0:
                i += 1
                break
        elif not started and ch == "\n":
            # значение без скобок на одной строке — на всякий случай, для этой
            # функции это не основной сценарий, но не должно зависать.
            break
        i += 1
    end = i

    new_text = text[:start] + new_value_literal + text[end:]
    safe_files.atomic_write_text(config_path, new_text, validate=safe_files.validate_python_syntax)
    safe_files.restrict_to_current_user(config_path)


def _format_risk_profiles(profiles: dict) -> str:
    """RISK_PROFILES использует ключи RiskProfile.XXX (enum) — repr() такого
    словаря НЕ является валидным Python-исходником (repr enum-члена выглядит
    как "<RiskProfile.CONSERVATIVE: 'conservative'>"). Поэтому собираем текст
    вручную в том же формате, что и оригинальный config.py."""
    lines = ["{"]
    for enum_member, params in profiles.items():
        parts = ", ".join(f"{k}={v!r}" for k, v in params.items())
        lines.append(f"    RiskProfile.{enum_member.name}: dict({parts}),")
    lines.append("}")
    return "\n".join(lines)


def _reload_cfg():
    """importlib.reload(cfg) + повторная расшифровка секретов (reload читает
    config.py заново с диска, где секреты снова в виде "enc:..." — нужно
    расшифровать их в памяти опять тем же паролем входа, что ввели при
    старте). Используй ВМЕСТО голого importlib.reload(cfg) везде в этом файле."""
    importlib.reload(cfg)
    pw = control.get_session_password()
    if pw:
        try:
            secure_store.unlock_config(cfg, pw)
        except ValueError as e:
            log.error("Не удалось расшифровать секреты после перечитывания config.py: %s", e)


def _migrate_legacy_secrets():
    """Одноразовая миграция при первом запуске новой версии: раньше
    config.py хранил пароль дашборда и остальные секреты (MT5/AI/новости)
    открытым текстом. Теперь пароль дашборда хранится только как хэш
    (DASHBOARD_PASSWORD_HASH), а остальные секреты — зашифрованы этим же
    паролем (см. secure_store.py). Если конфиг ещё старого формата —
    мигрируем автоматически ТЕМ ЖЕ паролем, что сейчас в файле, без каких-либо
    действий пользователя. Best-effort: если что-то пойдёт не так — оставляем
    всё как было (старый открытый режим), программа не ломается."""
    try:
        if getattr(cfg, "DASHBOARD_PASSWORD_HASH", ""):
            return  # уже мигрировано
        legacy_password = getattr(cfg, "DASHBOARD_PASSWORD", "")
        if not legacy_password:
            return  # нечем шифровать и не с чем сравнивать при входе — оставляем как есть

        salt = getattr(cfg, "SECURITY_SALT", "") or secure_store.new_salt()
        password_hash = secure_store.hash_password(legacy_password, salt)

        for field in ("MT5_PASSWORD", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            value = getattr(cfg, field, "")
            if isinstance(value, str) and value and not value.startswith(secure_store.ENC_PREFIX):
                encrypted = secure_store.encrypt_value(value, legacy_password, salt)
                _write_config_value(field, repr(encrypted))

        news_keys = getattr(cfg, "NEWS_API_KEYS", {}) or {}
        if isinstance(news_keys, dict) and news_keys:
            new_news_keys = {}
            changed = False
            for k, v in news_keys.items():
                if isinstance(v, str) and v and not v.startswith(secure_store.ENC_PREFIX):
                    new_news_keys[k] = secure_store.encrypt_value(v, legacy_password, salt)
                    changed = True
                else:
                    new_news_keys[k] = v
            if changed:
                _write_config_value("NEWS_API_KEYS", repr(new_news_keys))

        _write_config_value("SECURITY_SALT", repr(salt))
        _write_config_value("DASHBOARD_PASSWORD_HASH", repr(password_hash))
        # Сам пароль убираем из файла в открытом виде — теперь проверяется
        # через хэш выше, хранить его вторым разом открытым текстом незачем.
        _write_config_value("DASHBOARD_PASSWORD", '""')

        importlib.reload(cfg)
        log.info("config.py мигрирован на шифрование секретов (первый запуск новой версии программы).")
    except Exception as e:
        log.exception("Миграция шифрования секретов не удалась — работаю в старом (открытом) режиме: %s", e)


def _harden_files():
    """Best-effort защита файлов при старте программы: ограничение доступа
    (Windows ACL, только текущий пользователь) для config.py/лога/журнала
    сделок, плюс проверка целостности журнала сделок (сверка с sha256 от
    прошлого запуска — предупреждает, если файл менялся снаружи программы)."""
    try:
        config_path = os.path.join(_app_dir, "config.py")
        log_path = LOG_FILE
        trades_path = os.path.join(_app_dir, getattr(cfg, "LOG_CSV_PATH", "trades_log.csv"))

        for p in (config_path, log_path, trades_path):
            safe_files.restrict_to_current_user(p)

        if os.path.exists(trades_path) and not safe_files.check_integrity(trades_path):
            msg = ("trades_log.csv изменился СНАРУЖИ программы с прошлого запуска. "
                   "Если это не ты правил файл руками — проверь компьютер на посторонний доступ.")
            log.warning(msg)
            control.push_notification("Проверка целостности", msg)
        if os.path.exists(trades_path):
            safe_files.mark_integrity_current(trades_path)
    except Exception as e:
        log.warning("Не удалось выполнить проверку целостности/ограничение доступа к файлам: %s", e)


# ---- "Запомнить пароль" на экране входа (Windows DPAPI, см. secure_store.py) ----
_REMEMBER_PATH = os.path.join(_app_dir, ".login_remember")


def _save_remembered_password(password: str):
    try:
        blob = secure_store.dpapi_protect(password.encode("utf-8"))
        with open(_REMEMBER_PATH, "wb") as f:
            f.write(base64.b64encode(blob))
        safe_files.restrict_to_current_user(_REMEMBER_PATH)
    except Exception as e:
        log.warning("Не удалось сохранить пароль для автозаполнения (не критично): %s", e)


def _load_remembered_password() -> str:
    try:
        if not os.path.exists(_REMEMBER_PATH):
            return ""
        with open(_REMEMBER_PATH, "rb") as f:
            blob = base64.b64decode(f.read())
        return secure_store.dpapi_unprotect(blob).decode("utf-8")
    except Exception:
        return ""


def _clear_remembered_password():
    try:
        if os.path.exists(_REMEMBER_PATH):
            os.remove(_REMEMBER_PATH)
    except Exception:
        pass


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("860x660")
        self.root.minsize(780, 580)

        self._apply_theme()

        self.stop_event = None
        self.bot_thread = None
        self.tray_icon = None
        self._dashboard_started = False
        self.chat_history = []

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        if cfg.USE_WEB_DASHBOARD and not self._dashboard_started:
            try:
                bot_engine.start_dashboard_thread()
                self._dashboard_started = True
            except Exception as e:
                log.exception("Не удалось поднять веб-дашборд: %s", e)

        if TRAY_AVAILABLE:
            self._start_tray()

        self._refresh_loop()

        # Автозапуск торгового цикла вместе с программой — не нужно нажимать
        # "Старт" руками. Кнопки Старт/Стоп остаются для ручной остановки/
        # перезапуска. Небольшая задержка — чтобы окно успело отрисоваться.
        self.root.after(400, self.start_bot)

    # ---- тема оформления --------------------------------------------------
    def _apply_theme(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        bg, fg, field = "#1b1b1b", "#eee", "#242424"
        self.root.configure(bg=bg)
        style.configure(".", background=bg, foreground=fg, fieldbackground=field)
        style.configure("TNotebook", background=bg)
        style.configure("TNotebook.Tab", background="#2a2a2a", foreground=fg, padding=(12, 6))
        style.map("TNotebook.Tab", background=[("selected", "#3a3a3a")])
        style.configure("Treeview", background=field, fieldbackground=field, foreground=fg, rowheight=22)
        style.configure("Treeview.Heading", background="#2a2a2a", foreground=fg)
        style.configure("TLabelframe", background=bg, foreground=fg)
        style.configure("TLabelframe.Label", background=bg, foreground=fg)

    # ---- интерфейс: вкладки -------------------------------------------------
    def _build_ui(self):
        credit_label = ttk.Label(self.root, text="made by Viacheslav.Y.",
                                  foreground="#666", font=("Segoe UI", 8))
        credit_label.pack(side="bottom", anchor="e", padx=8, pady=2)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=6)

        tab_overview = ttk.Frame(self.notebook)
        tab_broker = ttk.Frame(self.notebook)
        tab_symbols = ttk.Frame(self.notebook)
        tab_positions = ttk.Frame(self.notebook)
        tab_log = ttk.Frame(self.notebook)
        tab_equity = ttk.Frame(self.notebook)
        tab_config = ttk.Frame(self.notebook)
        tab_news = ttk.Frame(self.notebook)
        tab_chat = ttk.Frame(self.notebook)
        tab_help = ttk.Frame(self.notebook)

        self.tab_frames = {
            "Обзор": tab_overview, "Брокер": tab_broker, "Символы": tab_symbols,
            "Сделки": tab_positions, "Лог": tab_log, "Equity": tab_equity,
            "Настройка": tab_config,
            "Новости": tab_news, "Chat AI": tab_chat,
            "Как пользоваться": tab_help,
        }
        for name, frame in self.tab_frames.items():
            self.notebook.add(frame, text=name)

        self._build_tab_overview(tab_overview)
        self._build_tab_broker(tab_broker)
        self._build_tab_symbols(tab_symbols)
        self._build_tab_positions(tab_positions)
        self._build_tab_log(tab_log)
        self._build_tab_equity(tab_equity)
        self._build_tab_config(tab_config)
        self._build_tab_news(tab_news)
        self._build_tab_chat(tab_chat)
        self._build_tab_help(tab_help)

        self._apply_ui_mode(initial=True)

    # ---- Простой/Продвинутый режим -----------------------------------------
    def _apply_ui_mode(self, initial: bool = False):
        is_advanced = (self.ui_mode_var.get() == "Продвинутый")
        for name in ADVANCED_TAB_NAMES:
            frame = self.tab_frames.get(name)
            if frame is None:
                continue
            shown = str(frame) in self.notebook.tabs()
            if is_advanced and not shown:
                self.notebook.add(frame, text=name)
            elif not is_advanced and shown:
                self.notebook.hide(frame)
        if not initial:
            try:
                _write_config_value("UI_MODE", repr("advanced" if is_advanced else "simple"))
            except Exception:
                pass

    # ---- вкладка "Обзор" ----------------------------------------------------
    def _build_tab_overview(self, parent):
        pad = {"padx": 10, "pady": 6}

        ttk.Label(parent, text=APP_TITLE, font=("Segoe UI", 16, "bold")).pack(**pad)

        mode_frame = ttk.Frame(parent)
        mode_frame.pack(**pad)
        ttk.Label(mode_frame, text="Режим интерфейса:").grid(row=0, column=0, padx=(0, 6))
        initial_mode = "Продвинутый" if getattr(cfg, "UI_MODE", "simple") == "advanced" else "Простой"
        self.ui_mode_var = tk.StringVar(value=initial_mode)
        mode_combo = ttk.Combobox(mode_frame, textvariable=self.ui_mode_var,
                                   values=["Простой", "Продвинутый"], state="readonly", width=16)
        mode_combo.grid(row=0, column=1)
        mode_combo.bind("<<ComboboxSelected>>", lambda e: self._apply_ui_mode())

        self.status_var = tk.StringVar(value="Остановлен")
        ttk.Label(parent, textvariable=self.status_var, font=("Segoe UI", 11)).pack(**pad)

        # Предупреждение "почему сделки не открываются" — видно, только если
        # реально есть проблема с разрешением на торговлю (AutoTrading и т.п.).
        self.trade_warning_var = tk.StringVar(value="")
        self.trade_warning_label = ttk.Label(parent, textvariable=self.trade_warning_var,
                                              foreground="#e57373", wraplength=780, justify="left")
        self.trade_warning_label.pack(**pad)

        btn_frame = ttk.Frame(parent)
        btn_frame.pack(**pad)
        self.start_btn = ttk.Button(btn_frame, text="▶  Старт", command=self.start_bot)
        self.start_btn.grid(row=0, column=0, padx=5)
        self.stop_btn = ttk.Button(btn_frame, text="■  Стоп", command=self.stop_bot, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=5)
        ttk.Button(btn_frame, text="✕  Полный выход", command=self.full_exit).grid(row=0, column=2, padx=5)

        info_frame = ttk.LabelFrame(parent, text="Счёт")
        info_frame.pack(fill="x", **pad)
        self.info_var = tk.StringVar(value="Бот ещё не запускался.")
        ttk.Label(info_frame, textvariable=self.info_var, justify="left").pack(anchor="w", padx=8, pady=4)

        stats_frame = ttk.LabelFrame(parent, text="Статистика")
        stats_frame.pack(fill="x", **pad)
        self.stats_var = tk.StringVar(value="—")
        ttk.Label(stats_frame, textvariable=self.stats_var, justify="left").pack(anchor="w", padx=8, pady=4)

        action_frame = ttk.Frame(parent)
        action_frame.pack(**pad)
        ttk.Button(action_frame, text="Открыть логи", command=self.open_logs).grid(row=0, column=0, padx=5, pady=2)
        ttk.Button(action_frame, text="Открыть config.py", command=self.open_config).grid(row=0, column=1, padx=5, pady=2)
        ttk.Button(action_frame, text="Дашборд в браузере (для телефона)",
                   command=self.open_dashboard).grid(row=0, column=2, padx=5, pady=2)
        ttk.Button(action_frame, text="Экспорт в Excel", command=self.export_excel).grid(row=1, column=0, padx=5, pady=2)

        self.autostart_var = tk.BooleanVar(value=_is_autostart_enabled())
        ttk.Checkbutton(parent, text="Запускать бота вместе с Windows", variable=self.autostart_var,
                        command=self._toggle_autostart).pack(**pad)

        ttk.Label(parent, text=f"С телефона по Wi-Fi: http://<IP-компьютера>:{cfg.DASHBOARD_PORT}",
                  foreground="#888", wraplength=600, justify="left").pack(**pad)

    # ---- вкладка "Брокер" ----------------------------------------------------
    def _build_tab_broker(self, parent):
        pad = {"padx": 10, "pady": 6}

        ttk.Label(parent, text="Подключение к брокеру (любой MT5-брокер)",
                  font=("Segoe UI", 12, "bold")).pack(**pad)
        ttk.Label(parent, foreground="#888", wraplength=680, justify="left", text=
                  "Впиши сервер/логин/пароль торгового счёта — как при входе в MetaTrader 5. "
                  "Терминал MT5 всё равно должен быть УСТАНОВЛЕН на этом компьютере, но держать его "
                  "открытым и залогиненным заранее уже не обязательно — программа сделает это сама."
                  ).pack(**pad)

        self.use_existing_var = tk.BooleanVar(value=not bool(getattr(cfg, "MT5_LOGIN", 0)))
        ttk.Checkbutton(parent, text="Использовать уже открытый и залогиненный терминал (без пароля в файле)",
                        variable=self.use_existing_var, command=self._toggle_broker_fields).pack(anchor="w", **pad)

        form = ttk.Frame(parent)
        form.pack(fill="x", **pad)

        ttk.Label(form, text="Сервер брокера:").grid(row=0, column=0, sticky="w", pady=4)
        self.server_var = tk.StringVar(value=str(getattr(cfg, "MT5_SERVER", "") or ""))
        self.server_entry = ttk.Entry(form, textvariable=self.server_var, width=34)
        self.server_entry.grid(row=0, column=1, pady=4, sticky="w")

        ttk.Label(form, text="Логин (номер счёта):").grid(row=1, column=0, sticky="w", pady=4)
        self.login_var = tk.StringVar(value=str(getattr(cfg, "MT5_LOGIN", "") or ""))
        self.login_entry = ttk.Entry(form, textvariable=self.login_var, width=34)
        self.login_entry.grid(row=1, column=1, pady=4, sticky="w")

        ttk.Label(form, text="Пароль:").grid(row=2, column=0, sticky="w", pady=4)
        self.password_var = tk.StringVar(value=str(getattr(cfg, "MT5_PASSWORD", "") or ""))
        self.password_entry = ttk.Entry(form, textvariable=self.password_var, width=34, show="*")
        self.password_entry.grid(row=2, column=1, pady=4, sticky="w")

        ttk.Label(form, text="Путь к terminal64.exe (необязательно):").grid(row=3, column=0, sticky="w", pady=4)
        self.term_path_var = tk.StringVar(value=str(getattr(cfg, "MT5_TERMINAL_PATH", "") or ""))
        self.term_path_entry = ttk.Entry(form, textvariable=self.term_path_var, width=34)
        self.term_path_entry.grid(row=3, column=1, pady=4, sticky="w")
        ttk.Button(form, text="Обзор...", command=self._browse_terminal_path).grid(row=3, column=2, padx=6)

        btn_frame = ttk.Frame(parent)
        btn_frame.pack(**pad)
        ttk.Button(btn_frame, text="Сохранить", command=self.save_broker_settings).grid(row=0, column=0, padx=5)
        self.test_conn_btn = ttk.Button(btn_frame, text="Проверить подключение", command=self.test_connection)
        self.test_conn_btn.grid(row=0, column=1, padx=5)

        self.broker_status_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.broker_status_var, wraplength=680, justify="left").pack(**pad)

        self._toggle_broker_fields()

    def _toggle_broker_fields(self):
        state = "disabled" if self.use_existing_var.get() else "normal"
        for w in (self.server_entry, self.login_entry, self.password_entry):
            w.config(state=state)

    def _browse_terminal_path(self):
        path = filedialog.askopenfilename(
            title="Выбери terminal64.exe",
            filetypes=[("MetaTrader 5", "terminal64.exe"), ("Все файлы", "*.*")],
        )
        if path:
            self.term_path_var.set(path)

    def save_broker_settings(self):
        try:
            if self.use_existing_var.get():
                _write_config_value("MT5_LOGIN", "0")
                _write_config_value("MT5_PASSWORD", '""')
                _write_config_value("MT5_SERVER", '""')
            else:
                login_text = self.login_var.get().strip()
                server_text = self.server_var.get().strip()
                if not login_text.isdigit():
                    messagebox.showerror(APP_TITLE, "Логин должен быть числом (номер торгового счёта).")
                    return
                if not server_text:
                    messagebox.showerror(APP_TITLE, "Укажи сервер брокера (как в MetaTrader 5 при входе).")
                    return
                _write_config_value("MT5_LOGIN", login_text)
                # Пароль брокера храним в config.py зашифрованным паролем
                # входа (см. secure_store.py) — на диске он не читается
                # открытым текстом, даже если файл скопировать на другой
                # компьютер.
                raw_password = self.password_var.get()
                pw = control.get_session_password()
                salt = getattr(cfg, "SECURITY_SALT", "")
                stored_password = (
                    secure_store.encrypt_value(raw_password, pw, salt) if (pw and salt) else raw_password
                )
                _write_config_value("MT5_PASSWORD", repr(stored_password))
                _write_config_value("MT5_SERVER", repr(server_text))
            _write_config_value("MT5_TERMINAL_PATH", repr(self.term_path_var.get().strip()))
            _reload_cfg()
            messagebox.showinfo(APP_TITLE, "Настройки подключения сохранены.")
        except Exception as e:
            log.exception("Не удалось сохранить настройки брокера: %s", e)
            messagebox.showerror(APP_TITLE, f"Не удалось сохранить: {e}")

    def test_connection(self):
        if self.bot_thread and self.bot_thread.is_alive():
            messagebox.showinfo(APP_TITLE, "Останови бота перед проверкой подключения.")
            return
        self.test_conn_btn.config(state="disabled")
        self.broker_status_var.set("Проверяю подключение...")
        threading.Thread(target=self._test_connection_worker, daemon=True).start()

    def _test_connection_worker(self):
        try:
            acc = bot_engine.mt5c.connect()
            msg = f"Подключено: счёт {acc.login} ({acc.server}), баланс {acc.balance:.2f} {acc.currency}"
            bot_engine.mt5c.disconnect()
            self.root.after(0, lambda: self.broker_status_var.set(msg))
        except Exception as e:
            err = str(e)
            self.root.after(0, lambda: self.broker_status_var.set(f"Ошибка: {err}"))
        finally:
            self.root.after(0, lambda: self.test_conn_btn.config(state="normal"))

    # ---- вкладка "Символы" ---------------------------------------------------
    def _build_tab_symbols(self, parent):
        ttk.Label(parent, text="Двойной клик по 'Вкл' — включить/выключить пару. По 'Лот' — задать "
                               "фиксированный лот. По 'Символ' — мини-график цены.",
                  foreground="#888", wraplength=800, justify="left").pack(padx=10, pady=6, anchor="w")

        add_frame = ttk.Frame(parent)
        add_frame.pack(fill="x", padx=10, pady=(0, 6))
        ttk.Label(add_frame, text="Добавить символ:").grid(row=0, column=0, padx=(0, 6))
        self.new_symbol_var = tk.StringVar()
        # Combobox вместо свободного текста — список тянется из реального
        # списка символов брокера (mt5.symbols_get(), см. mt5_connector.get_all_symbols
        # и main.py._refresh_available_symbols_cache). Поле остаётся редактируемым
        # (не readonly), чтобы можно было ввести символ, которого ещё нет в кэше,
        # но по умолчанию показывает подтверждённые доступные у брокера пары —
        # это убирает частую ошибку "добавил пару, а она не работает" из-за
        # опечатки/неверного суффикса.
        self.symbol_picker = ttk.Combobox(add_frame, textvariable=self.new_symbol_var, width=18)
        self.symbol_picker.grid(row=0, column=1, padx=(0, 6))
        self.symbol_picker.bind("<Return>", lambda e: self.add_symbol())
        ttk.Button(add_frame, text="Добавить", command=self.add_symbol).grid(row=0, column=2, padx=4)
        ttk.Button(add_frame, text="Удалить выбранный", command=self.remove_selected_symbol).grid(row=0, column=3, padx=4)
        self.available_symbols_var = tk.StringVar(
            value="Список пар брокера ещё не загружен (появится, когда бот запущен и подключён к MT5)."
        )
        ttk.Label(add_frame, textvariable=self.available_symbols_var, foreground="#888").grid(
            row=1, column=0, columnspan=4, sticky="w", padx=(0, 6), pady=(4, 0))

        self.symbols_columns = ("enabled", "symbol", "lot", "buy", "sell", "regime", "ai", "custom", "multi", "learn", "paused", "reject")
        headings = ("Вкл", "Символ", "Лот", "BUY", "SELL", "Режим", "AI", "Своя стратегия", "Индикаторы", "Автообучение", "Пауза", "Отказ")
        self.symbols_tree = ttk.Treeview(parent, columns=self.symbols_columns, show="headings", height=12)
        for col, head in zip(self.symbols_columns, headings):
            self.symbols_tree.heading(col, text=head)
            self.symbols_tree.column(col, width=90, anchor="center")
        self.symbols_tree.column("reject", width=220, anchor="w")
        self.symbols_tree.column("symbol", width=90, anchor="w")
        self.symbols_tree.pack(fill="both", expand=True, padx=10, pady=6)
        self.symbols_tree.bind("<Double-1>", self._on_symbols_double_click)

    def add_symbol(self):
        sym = self.new_symbol_var.get().strip().upper()
        if not sym:
            return
        symbols = list(cfg.SYMBOLS)
        if sym in symbols:
            messagebox.showinfo(APP_TITLE, f"{sym} уже есть в списке.")
            return

        available = list(getattr(self, "_available_symbols_cache", []) or [])
        if available and sym not in available:
            close_matches = [s for s in available if sym in s or s in sym][:8]
            hint = (
                f"Похожие пары у брокера: {', '.join(close_matches)}."
                if close_matches else
                "Похожих пар у брокера не найдено — проверь написание."
            )
            if not messagebox.askyesno(
                APP_TITLE,
                f"У брокера НЕТ символа '{sym}' в списке доступных ({len(available)} пар). {hint}\n\n"
                f"Всё равно добавить как есть?"
            ):
                return

        symbols.append(sym)
        self._write_symbols(symbols)
        self.new_symbol_var.set("")
        messagebox.showinfo(APP_TITLE, f"{sym} добавлен. Если бот запущен — подключится сам в течение "
                                        f"{getattr(cfg, 'CONFIG_RELOAD_CHECK_SECONDS', 15)} сек, "
                                        f"иначе — при следующем нажатии Старт.")

    def remove_selected_symbol(self):
        sel = self.symbols_tree.selection()
        if not sel:
            messagebox.showinfo(APP_TITLE, "Выбери символ в таблице.")
            return
        sym = sel[0]
        symbols = [s for s in cfg.SYMBOLS if s != sym]
        self._write_symbols(symbols)
        messagebox.showinfo(APP_TITLE, f"{sym} убран из списка новых сделок "
                                        f"(уже открытые по нему позиции продолжат вестись).")

    def _write_symbols(self, symbols_list):
        literal = "[" + ", ".join(repr(s) for s in symbols_list) + "]"
        _write_config_value("SYMBOLS", literal)
        try:
            _reload_cfg()
        except Exception:
            pass

    def _on_symbols_double_click(self, event):
        region = self.symbols_tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        row_id = self.symbols_tree.identify_row(event.y)
        col_id = self.symbols_tree.identify_column(event.x)
        if not row_id or not col_id:
            return
        try:
            col_index = int(col_id.replace("#", "")) - 1
            col_name = self.symbols_columns[col_index]
        except (ValueError, IndexError):
            return
        sym = row_id
        if col_name == "enabled":
            control.set_symbol_enabled(sym, not control.is_symbol_enabled(sym))
        elif col_name == "lot":
            current = control.get_lot_override(sym) or 0
            new_lot = simpledialog.askfloat(
                "Лот", f"Фиксированный лот для {sym} (0 = авторасчёт по риску):",
                initialvalue=current, minvalue=0, parent=self.root,
            )
            if new_lot is not None:
                control.set_lot_override(sym, new_lot)
        elif col_name == "symbol":
            self._open_price_chart(sym)
        self._refresh_symbols_tab()

    def _open_price_chart(self, symbol: str):
        win = tk.Toplevel(self.root)
        win.title(f"График цены — {symbol}")
        win.geometry("520x320")
        win.configure(bg="#1b1b1b")
        canvas = tk.Canvas(win, bg="#1b1b1b", highlightthickness=0)
        canvas.pack(fill="both", expand=True, padx=10, pady=10)

        def redraw():
            if not win.winfo_exists():
                return
            snap = ds.get_snapshot() or {}
            sy = (snap.get("symbols", {}) or {}).get(symbol, {})
            closes = sy.get("recent_closes", []) or []
            canvas.delete("all")
            w = canvas.winfo_width() or 480
            h = canvas.winfo_height() or 280
            if len(closes) < 2:
                canvas.create_text(10, h // 2, anchor="w", text="Копится история цены...", fill="#888")
            else:
                lo, hi = min(closes), max(closes)
                rng = (hi - lo) or 1e-9
                step_x = w / (len(closes) - 1)
                points = []
                for i, v in enumerate(closes):
                    x = i * step_x
                    y = h - ((v - lo) / rng) * (h - 30) - 15
                    points.extend([x, y])
                canvas.create_line(*points, fill="#4caf50", width=2)
                canvas.create_text(35, h - 10, text=f"мин: {lo:.5f}", fill="#888", anchor="w")
                canvas.create_text(35, 10, text=f"макс: {hi:.5f}", fill="#888", anchor="w")
            win.after(3000, redraw)

        redraw()

    def _refresh_symbols_tab(self):
        snap = ds.get_snapshot()
        symbols = snap.get("symbols", {}) if snap else {}

        available = (snap.get("available_symbols", []) if snap else []) or []
        if available != getattr(self, "_available_symbols_cache", None):
            self._available_symbols_cache = available
            self.symbol_picker["values"] = available
            if available:
                self.available_symbols_var.set(f"Доступно у брокера: {len(available)} пар (выпадающий список).")
            else:
                self.available_symbols_var.set(
                    "Список пар брокера ещё не загружен (появится, когда бот запущен и подключён к MT5)."
                )

        for item in self.symbols_tree.get_children():
            self.symbols_tree.delete(item)
        for sym, sy in symbols.items():
            enabled_txt = "✓" if sy.get("enabled", True) else "✗"
            lot_txt = sy.get("lot_override") or "авто"
            ai_txt = f"{sy.get('ai_direction') or '-'} ({round((sy.get('ai_confidence') or 0) * 100)}%)"
            self.symbols_tree.insert("", "end", iid=sym, values=(
                enabled_txt, sym, lot_txt,
                round(sy.get("buy_score", 0), 1), round(sy.get("sell_score", 0), 1),
                sy.get("regime", "-"), ai_txt, round(sy.get("custom_score", 0), 1),
                round(sy.get("multi_indicator_score", 0), 1),
                sy.get("learning_status", "-"),
                sy.get("paused_until") or "-", sy.get("reject_reason", "-"),
            ))

    # ---- вкладка "Сделки" ------------------------------------------------------
    def _build_tab_positions(self, parent):
        ttk.Label(parent, foreground="#888", wraplength=800, justify="left", text=
                  "Показаны ВСЕ открытые позиции счёта — и этого бота, и открытые "
                  "вручную в терминале MT5 (колонка «Источник»)."
                  ).pack(padx=10, pady=(8, 2), anchor="w")
        cols = ("ticket", "symbol", "type", "volume", "price_open", "price_current", "sl", "tp",
                "profit", "open_time", "source")
        headings = ("Тикет", "Символ", "Тип", "Лот", "Вход", "Текущая", "SL", "TP", "Профит",
                    "Время открытия", "Источник")
        self.positions_tree = ttk.Treeview(parent, columns=cols, show="headings", height=12)
        for col, head in zip(cols, headings):
            self.positions_tree.heading(col, text=head)
            self.positions_tree.column(col, width=85, anchor="center")
        self.positions_tree.pack(fill="both", expand=True, padx=10, pady=6)

        btn_row = ttk.Frame(parent)
        btn_row.pack(fill="x", padx=10, pady=6)
        ttk.Button(btn_row, text="Закрыть выбранную сделку", command=self.close_selected_position).pack(
            side="left")
        ttk.Button(btn_row, text="Закрыть ВСЕ сделки", command=self.close_all_positions).pack(
            side="left", padx=(8, 0))
        ttk.Button(btn_row, text="Закрыть прибыльные", command=self.close_profitable_positions).pack(
            side="left", padx=(8, 0))
        ttk.Button(btn_row, text="Закрыть убыточные", command=self.close_losing_positions).pack(
            side="left", padx=(8, 0))

    def _refresh_positions_tab(self):
        snap = ds.get_snapshot()
        positions = snap.get("positions", []) if snap else []
        for item in self.positions_tree.get_children():
            self.positions_tree.delete(item)
        for p in positions:
            source = "Бот" if p.get("is_bot", True) else "Ручная"
            self.positions_tree.insert("", "end", iid=str(p["ticket"]), values=(
                p["ticket"], p["symbol"], p["type"], p["volume"],
                p["price_open"], p.get("price_current", "-"), p["sl"], p["tp"],
                round(p["profit"], 2), p.get("open_time", "-"), source,
            ))

    def close_selected_position(self):
        sel = self.positions_tree.selection()
        if not sel:
            messagebox.showinfo(APP_TITLE, "Выбери сделку в таблице.")
            return
        ticket = int(sel[0])
        if messagebox.askyesno(APP_TITLE, f"Закрыть позицию #{ticket} по рынку?"):
            control.request_close(ticket)

    def close_all_positions(self):
        count = len(self.positions_tree.get_children())
        if count == 0:
            messagebox.showinfo(APP_TITLE, "Нет открытых сделок.")
            return
        if messagebox.askyesno(
            APP_TITLE,
            f"Закрыть АБСОЛЮТНО ВСЕ открытые позиции счёта ({count} шт.) по рынку? "
            f"Это касается и сделок бота, и открытых вручную в терминале MT5. "
            f"Действие нельзя отменить.",
        ):
            control.request_close_all()

    def _count_positions_by_profit(self, want_profitable: bool) -> int:
        count = 0
        for iid in self.positions_tree.get_children():
            values = self.positions_tree.item(iid, "values")
            try:
                profit = float(values[8])  # колонка "profit", см. cols в _build_tab_positions
            except (IndexError, ValueError):
                continue
            if (profit >= 0) == want_profitable:
                count += 1
        return count

    def close_profitable_positions(self):
        count = self._count_positions_by_profit(want_profitable=True)
        if count == 0:
            messagebox.showinfo(APP_TITLE, "Нет прибыльных сделок сейчас.")
            return
        if messagebox.askyesno(
            APP_TITLE,
            f"Закрыть ВСЕ прибыльные сейчас позиции счёта ({count} шт.) по рынку? "
            f"Это касается и сделок бота, и открытых вручную в терминале MT5.",
        ):
            control.request_close_profitable()

    def close_losing_positions(self):
        count = self._count_positions_by_profit(want_profitable=False)
        if count == 0:
            messagebox.showinfo(APP_TITLE, "Нет убыточных сделок сейчас.")
            return
        if messagebox.askyesno(
            APP_TITLE,
            f"Закрыть ВСЕ убыточные сейчас позиции счёта ({count} шт.) по рынку? "
            f"Это касается и сделок бота, и открытых вручную в терминале MT5. "
            f"Действие зафиксирует текущий убыток по ним.",
        ):
            control.request_close_losing()

    # ---- вкладка "Лог" -----------------------------------------------------------
    def _build_tab_log(self, parent):
        ttk.Label(parent, text="Журнал этого бота", font=("Segoe UI", 11, "bold")).pack(
            padx=10, pady=(8, 2), anchor="w")
        cols = ("Time", "Event", "Symbol", "Direction", "Price", "SL", "TP", "Lot", "Score", "Profit")
        self.log_tree = ttk.Treeview(parent, columns=cols, show="headings", height=10)
        for col in cols:
            self.log_tree.heading(col, text=col)
            self.log_tree.column(col, width=80, anchor="center")
        self.log_tree.pack(fill="both", expand=False, padx=10, pady=(0, 6))

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=10, pady=6)

        top_row = ttk.Frame(parent)
        top_row.pack(fill="x", padx=10)
        ttk.Label(top_row, text="Синхронизация с MetaTrader (все сделки счёта, вкл. ручные)",
                  font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Button(top_row, text="Обновить", command=self._refresh_log_tab).pack(side="right")

        self.mt5_history_stats_var = tk.StringVar(value="Синхронизация ещё не выполнялась...")
        ttk.Label(parent, textvariable=self.mt5_history_stats_var, foreground="#888",
                  wraplength=800, justify="left").pack(padx=10, pady=(2, 6), anchor="w")

        mt5_cols = ("ticket", "time", "symbol", "type", "volume", "price", "profit", "source")
        mt5_headings = ("Тикет", "Время", "Символ", "Тип", "Лот", "Цена", "Профит", "Источник")
        self.mt5_history_tree = ttk.Treeview(parent, columns=mt5_cols, show="headings", height=10)
        for col, head in zip(mt5_cols, mt5_headings):
            self.mt5_history_tree.heading(col, text=head)
            self.mt5_history_tree.column(col, width=85, anchor="center")
        self.mt5_history_tree.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _refresh_log_tab(self):
        rows = []
        try:
            path = cfg.LOG_CSV_PATH
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    reader = list(csv.reader(f, delimiter=";"))
                if len(reader) > 1:
                    data = reader[1:]
                    rows = list(reversed(data[-50:]))
        except Exception:
            pass
        for item in self.log_tree.get_children():
            self.log_tree.delete(item)
        for row in rows:
            self.log_tree.insert("", "end", values=row)

        snap = ds.get_snapshot() or {}
        history = snap.get("mt5_history", [])
        stats = snap.get("mt5_history_stats", {})
        for item in self.mt5_history_tree.get_children():
            self.mt5_history_tree.delete(item)
        for d in history[:100]:
            source = "Бот" if d.get("is_bot", True) else "Ручная"
            self.mt5_history_tree.insert("", "end", values=(
                d["ticket"], d["time"], d["symbol"], d["type"], d["volume"], d["price"],
                d["profit"], source,
            ))
        if stats:
            self.mt5_history_stats_var.set(
                f"За последние {stats.get('days', '-')} дн.: сделок {stats.get('total_trades', 0)}, "
                f"винрейт {stats.get('win_rate', 0)}%, профит-фактор {stats.get('profit_factor', 0)}, "
                f"вал. прибыль {stats.get('gross_profit', 0)}, вал. убыток {stats.get('gross_loss', 0)} "
                f"— данные напрямую из истории MT5 (100% совпадает с брокером)."
            )
        elif not history:
            self.mt5_history_stats_var.set(
                "История MT5 ещё пуста или синхронизация ещё не прошла (обновляется раз в минуту)."
            )

    # ---- вкладка "Equity" ----------------------------------------------------------
    def _build_tab_equity(self, parent):
        self.equity_canvas = tk.Canvas(parent, bg="#1b1b1b", highlightthickness=0)
        self.equity_canvas.pack(fill="both", expand=True, padx=10, pady=10)

    def _redraw_equity_canvas(self):
        c = self.equity_canvas
        c.delete("all")
        w = c.winfo_width() or 600
        h = c.winfo_height() or 300
        hist = control.get_equity_history()
        if len(hist) < 2:
            c.create_text(10, h // 2, anchor="w", text="Копится история...", fill="#888")
            return
        values = [p["equity"] for p in hist]
        lo, hi = min(values), max(values)
        rng = (hi - lo) or 1
        step_x = w / (len(values) - 1)
        points = []
        for i, v in enumerate(values):
            x = i * step_x
            y = h - ((v - lo) / rng) * (h - 30) - 15
            points.extend([x, y])
        if len(points) >= 4:
            c.create_line(*points, fill="#4caf50", width=2, smooth=False)
        c.create_text(35, h - 10, text=f"мин: {lo:.2f}", fill="#888", anchor="w")
        c.create_text(35, 10, text=f"макс: {hi:.2f}", fill="#888", anchor="w")

    # ---- вкладка "Настройка" (всегда видима — быстрые настройки + ВСЕ параметры) ----
    def _build_tab_config(self, parent):
        """Единая вкладка "Настройка" — видна и в простом, и в продвинутом
        режиме (не прячется), чтобы настройки было невозможно "не найти".
        Сверху — быстрые переключатели (профиль/режим/пауза/звук), ниже —
        полный список input-параметров (как в MQL5-советнике) с прокруткой."""
        self._build_tab_settings(parent)
        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=10, pady=(4, 8))
        self._build_tab_params(parent)

    def _build_tab_settings(self, parent):
        pad = {"padx": 10, "pady": 8}

        ttk.Label(parent, text="Профиль риска", font=("Segoe UI", 11, "bold")).pack(anchor="w", **pad)
        self.profile_combo = ttk.Combobox(parent, values=[label for label, _ in PROFILE_OPTIONS],
                                           state="readonly", width=30)
        self.profile_combo.pack(anchor="w", padx=10)
        current_profile = (control.get_risk_profile() or cfg.RISK_PROFILE).value
        for label, value in PROFILE_OPTIONS:
            if value == current_profile:
                self.profile_combo.set(label)
        ttk.Button(parent, text="Применить профиль", command=self._apply_profile).pack(anchor="w", **pad)

        ttk.Label(parent, text="Режим торговли", font=("Segoe UI", 11, "bold")).pack(anchor="w", **pad)
        self.mode_combo = ttk.Combobox(parent, values=[label for label, _ in MODE_OPTIONS],
                                        state="readonly", width=30)
        self.mode_combo.pack(anchor="w", padx=10)
        current_mode = (control.get_trading_mode() or cfg.TRADING_MODE).value
        for label, value in MODE_OPTIONS:
            if value == current_mode:
                self.mode_combo.set(label)
        ttk.Button(parent, text="Применить режим", command=self._apply_mode).pack(anchor="w", **pad)

        self.pause_btn = ttk.Button(parent, text="Пауза (новые сделки)", command=self._toggle_pause)
        self.pause_btn.pack(anchor="w", **pad)

        self.sound_var = tk.BooleanVar(value=getattr(cfg, "USE_SOUND_NOTIFICATIONS", True))
        ttk.Checkbutton(parent, text="Звук + всплывающие уведомления о сделках", variable=self.sound_var,
                        command=self._toggle_sound).pack(anchor="w", **pad)

    def _toggle_sound(self):
        _write_config_value("USE_SOUND_NOTIFICATIONS", str(self.sound_var.get()))
        try:
            _reload_cfg()
        except Exception:
            pass

    def _apply_profile(self):
        label = self.profile_combo.get()
        value = next((v for l, v in PROFILE_OPTIONS if l == label), None)
        if value:
            control.set_risk_profile(cfg.RiskProfile(value))
            messagebox.showinfo(APP_TITLE, f"Профиль риска изменён на «{label}».")

    def _apply_mode(self):
        label = self.mode_combo.get()
        value = next((v for l, v in MODE_OPTIONS if l == label), None)
        if value:
            control.set_trading_mode(cfg.TradingMode(value))
            messagebox.showinfo(APP_TITLE, f"Режим торговли изменён на «{label}».")

    def _toggle_pause(self):
        control.set_paused(not control.is_paused())

    # ---- вкладка "Параметры" (продвинутый режим — все input-параметры, как в советнике) ----
    def _build_tab_params(self, parent):
        ttk.Label(parent, text="Расширенные параметры (как input-параметры в советнике)",
                  font=("Segoe UI", 12, "bold")).pack(padx=10, pady=(10, 2), anchor="w")
        ttk.Label(parent, foreground="#888", wraplength=800, justify="left", text=
                  "Здесь можно вручную выставить КАЖДЫЙ параметр торговой логики — так же, "
                  "как input-параметры MQL5-советника. «Сохранить» применяет изменения сразу, "
                  "бот подхватит их на лету, без перезапуска. Единственное, что сюда не входит — "
                  "корреляции MARKET_CONTEXT редактируются отдельно ниже, а брокер/новости/AI-ключи "
                  "— на своих вкладках."
                  ).pack(padx=10, pady=(0, 8), anchor="w")

        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True, padx=6, pady=4)
        canvas = tk.Canvas(outer, bg="#1b1b1b", highlightthickness=0)
        vscroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        # ---- Профили риска — детальные параметры (RISK_PROFILES) ----
        profile_box = ttk.LabelFrame(inner, text="Профиль риска — детальные параметры")
        profile_box.pack(fill="x", padx=6, pady=(4, 10))
        ttk.Label(profile_box, text="Профиль:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.param_profile_combo = ttk.Combobox(
            profile_box, values=[label for label, _ in PROFILE_OPTIONS], state="readonly", width=24)
        self.param_profile_combo.set(PROFILE_OPTIONS[1][0])
        self.param_profile_combo.grid(row=0, column=1, sticky="w", padx=6, pady=4)
        self.param_profile_combo.bind("<<ComboboxSelected>>", lambda e: self._load_profile_fields())

        self.profile_field_vars = {}
        profile_fields_frame = ttk.Frame(profile_box)
        profile_fields_frame.grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        self._build_profile_fields(profile_fields_frame)
        self._load_profile_fields()

        ttk.Button(profile_box, text="Сохранить профиль", command=self.save_profile_fields).grid(
            row=2, column=0, sticky="w", padx=6, pady=(2, 10))

        # ---- Контекст рынка — корреляции по инструментам (MARKET_CONTEXT) ----
        context_box = ttk.LabelFrame(inner, text="Контекст рынка — корреляции по инструментам (до 3 на символ)")
        context_box.pack(fill="x", padx=6, pady=(4, 10))
        ttk.Label(context_box, foreground="#888", wraplength=760, justify="left", text=
                  "Для каждого торгуемого символа можно задать до 3 коррелирующих инструментов "
                  "(например, индекс доллара для золота). Пустое поле = слот не используется. "
                  "Работает, только если включён общий флаг «Учитывать коррелирующие инструменты» "
                  "в разделе «Контекст рынка» ниже."
                  ).pack(anchor="w", padx=6, pady=(4, 6))
        self.context_symbol_vars = {}
        for sym in cfg.SYMBOLS:
            row = ttk.Frame(context_box)
            row.pack(fill="x", padx=6, pady=2)
            ttk.Label(row, text=sym, width=12).pack(side="left")
            slots = list(cfg.MARKET_CONTEXT.get(sym, []))
            slot_vars = []
            for slot_i in range(3):
                sym_var = tk.StringVar(value=slots[slot_i][0] if slot_i < len(slots) else "")
                corr_var = tk.StringVar(value=slots[slot_i][1] if slot_i < len(slots) else "positive")
                ttk.Entry(row, textvariable=sym_var, width=10).pack(side="left", padx=(4, 2))
                ttk.Combobox(row, textvariable=corr_var, values=["positive", "negative"],
                             state="readonly", width=9).pack(side="left", padx=(0, 8))
                slot_vars.append((sym_var, corr_var))
            self.context_symbol_vars[sym] = slot_vars
        ttk.Button(context_box, text="Сохранить контекст", command=self.save_market_context).pack(
            anchor="w", padx=6, pady=(4, 10))

        # ---- Плоские параметры, сгруппированные по разделам (как в config.py) ----
        groups = {}
        for key, ptype, group, label, choices in ADVANCED_PARAMS:
            groups.setdefault(group, []).append((key, ptype, label, choices))

        self.param_vars = {}
        for group, items in groups.items():
            box = ttk.LabelFrame(inner, text=group)
            box.pack(fill="x", padx=6, pady=4)
            for row_i, (key, ptype, label, choices) in enumerate(items):
                ttk.Label(box, text=label, wraplength=340, justify="left").grid(
                    row=row_i, column=0, sticky="w", padx=6, pady=3)
                current = getattr(cfg, key, "")
                if ptype == "bool":
                    var = tk.BooleanVar(value=bool(current))
                    ttk.Checkbutton(box, variable=var).grid(row=row_i, column=1, sticky="w", padx=6, pady=3)
                elif ptype == "choice":
                    var = tk.StringVar(value=str(current))
                    ttk.Combobox(box, textvariable=var, values=choices, state="readonly", width=14).grid(
                        row=row_i, column=1, sticky="w", padx=6, pady=3)
                else:
                    var = tk.StringVar(value=str(current))
                    ttk.Entry(box, textvariable=var, width=18).grid(row=row_i, column=1, sticky="w", padx=6, pady=3)
                self.param_vars[key] = (ptype, var)

        btn_frame = ttk.Frame(inner)
        btn_frame.pack(fill="x", padx=6, pady=14)
        ttk.Button(btn_frame, text="Сохранить все параметры", command=self.save_advanced_params).grid(
            row=0, column=0, padx=4)
        ttk.Button(btn_frame, text="Обновить из файла", command=self.reload_advanced_params).grid(
            row=0, column=1, padx=4)

    def _build_profile_fields(self, parent):
        for row_i, (field, ftype, label) in enumerate(RISK_PROFILE_FIELD_DEFS):
            ttk.Label(parent, text=label, wraplength=260, justify="left").grid(
                row=row_i, column=0, sticky="w", padx=6, pady=2)
            if ftype == "bool":
                var = tk.BooleanVar(value=False)
                ttk.Checkbutton(parent, variable=var).grid(row=row_i, column=1, sticky="w", padx=6, pady=2)
            else:
                var = tk.StringVar(value="")
                ttk.Entry(parent, textvariable=var, width=18).grid(row=row_i, column=1, sticky="w", padx=6, pady=2)
            self.profile_field_vars[field] = (ftype, var)

    def _current_profile_enum(self):
        label = self.param_profile_combo.get()
        value = next((v for l, v in PROFILE_OPTIONS if l == label), "balanced")
        return cfg.RiskProfile(value)

    def _load_profile_fields(self):
        profile_enum = self._current_profile_enum()
        params = cfg.RISK_PROFILES.get(profile_enum, {})
        for field, (ftype, var) in self.profile_field_vars.items():
            value = params.get(field, "")
            if ftype == "bool":
                var.set(bool(value))
            else:
                var.set(str(value))

    def save_profile_fields(self):
        profile_enum = self._current_profile_enum()
        new_params = {}
        errors = []
        for field, (ftype, var) in self.profile_field_vars.items():
            raw = var.get()
            try:
                if ftype == "bool":
                    new_params[field] = bool(raw)
                elif ftype == "int":
                    new_params[field] = int(raw)
                elif ftype == "float":
                    new_params[field] = float(raw)
                else:
                    new_params[field] = str(raw)
            except (TypeError, ValueError):
                errors.append(field)
        if errors:
            messagebox.showerror(APP_TITLE, "Некорректные значения: " + ", ".join(errors))
            return
        try:
            all_profiles = dict(cfg.RISK_PROFILES)
            all_profiles[profile_enum] = new_params
            _write_config_block("RISK_PROFILES", _format_risk_profiles(all_profiles))
            _reload_cfg()
            messagebox.showinfo(APP_TITLE, f"Профиль «{new_params.get('name', profile_enum.value)}» сохранён.")
        except Exception as e:
            log.exception("Не удалось сохранить профиль риска: %s", e)
            messagebox.showerror(APP_TITLE, f"Не удалось сохранить: {e}")

    def save_market_context(self):
        try:
            new_context = dict(cfg.MARKET_CONTEXT)
            for sym, slot_vars in self.context_symbol_vars.items():
                slots = []
                for sym_var, corr_var in slot_vars:
                    ctx_sym = sym_var.get().strip()
                    if ctx_sym:
                        slots.append((ctx_sym, corr_var.get()))
                new_context[sym] = slots
            _write_config_block("MARKET_CONTEXT", repr(new_context))
            _reload_cfg()
            messagebox.showinfo(APP_TITLE, "Контекст рынка сохранён.")
        except Exception as e:
            log.exception("Не удалось сохранить контекст рынка: %s", e)
            messagebox.showerror(APP_TITLE, f"Не удалось сохранить: {e}")

    def save_advanced_params(self):
        new_values = {}
        errors = []
        for key, (ptype, var) in self.param_vars.items():
            raw = var.get()
            try:
                if ptype == "bool":
                    new_values[key] = bool(raw)
                elif ptype == "int":
                    new_values[key] = int(raw)
                elif ptype == "float":
                    new_values[key] = float(raw)
                else:
                    new_values[key] = str(raw)
            except (TypeError, ValueError):
                errors.append(key)
        if errors:
            messagebox.showerror(APP_TITLE, "Некорректные значения в полях: " + ", ".join(errors))
            return

        tf_rank = {"M1": 1, "M5": 2, "M15": 3, "M30": 4, "H1": 5, "H4": 6, "D1": 7}
        tf = new_values.get("TIMEFRAME")
        trend_tf = new_values.get("TREND_TIMEFRAME")
        if tf and trend_tf and tf_rank.get(trend_tf, 0) <= tf_rank.get(tf, 0):
            messagebox.showerror(APP_TITLE, "Старший таймфрейм тренда должен быть СТАРШЕ рабочего таймфрейма.")
            return

        try:
            for key, value in new_values.items():
                literal = str(value) if isinstance(value, bool) else repr(value)
                _write_config_value(key, literal)
            _reload_cfg()
            messagebox.showinfo(APP_TITLE, "Параметры сохранены и применены.")
        except Exception as e:
            log.exception("Не удалось сохранить расширенные параметры: %s", e)
            messagebox.showerror(APP_TITLE, f"Не удалось сохранить: {e}")

    def reload_advanced_params(self):
        for key, (ptype, var) in self.param_vars.items():
            current = getattr(cfg, key, "")
            if ptype == "bool":
                var.set(bool(current))
            else:
                var.set(str(current))
        self._load_profile_fields()
        for sym, slot_vars in self.context_symbol_vars.items():
            slots = list(cfg.MARKET_CONTEXT.get(sym, []))
            for slot_i, (sym_var, corr_var) in enumerate(slot_vars):
                sym_var.set(slots[slot_i][0] if slot_i < len(slots) else "")
                corr_var.set(slots[slot_i][1] if slot_i < len(slots) else "positive")

    # ---- вкладка "Новости" ---------------------------------------------------------
    def _build_tab_news(self, parent):
        pad = {"padx": 10, "pady": 6}

        ttk.Label(parent, text="Источник новостей (API)", font=("Segoe UI", 12, "bold")).pack(**pad)
        ttk.Label(parent, foreground="#888", wraplength=780, justify="left", text=
                  "MT5 не даёт свой календарь из Python, поэтому новости берутся из внешнего API. "
                  "Список провайдеров ниже — универсальный: новые добавляются в news_providers.py, "
                  "и появятся в этом списке сами."
                  ).pack(**pad)

        form = ttk.Frame(parent)
        form.pack(fill="x", **pad)
        ttk.Label(form, text="Провайдер:").grid(row=0, column=0, sticky="w", pady=4)
        providers = list(news_providers.PROVIDERS.keys())
        self.news_provider_combo = ttk.Combobox(form, values=providers, state="readonly", width=20)
        current_provider = getattr(cfg, "NEWS_API_PROVIDER", providers[0] if providers else "")
        if current_provider in providers:
            self.news_provider_combo.set(current_provider)
        elif providers:
            self.news_provider_combo.set(providers[0])
        self.news_provider_combo.grid(row=0, column=1, sticky="w", pady=4)

        ttk.Label(form, text="API-ключ:").grid(row=1, column=0, sticky="w", pady=4)
        keys = getattr(cfg, "NEWS_API_KEYS", {}) or {}
        self.news_api_key_var = tk.StringVar(value=keys.get(current_provider, ""))
        ttk.Entry(form, textvariable=self.news_api_key_var, width=40, show="*").grid(row=1, column=1, sticky="w", pady=4)

        btn_frame = ttk.Frame(parent)
        btn_frame.pack(**pad)
        ttk.Button(btn_frame, text="Сохранить", command=self.save_news_settings).grid(row=0, column=0, padx=5)
        ttk.Button(btn_frame, text="Обновить список новостей", command=self.refresh_news_tab).grid(row=0, column=1, padx=5)

        self.news_status_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.news_status_var, foreground="#888", wraplength=780,
                  justify="left").pack(**pad)

        cols = ("time", "currency", "event", "impact", "actual", "estimate", "prev")
        headings = ("Время", "Валюта", "Событие", "Важность", "Факт", "Прогноз", "Пред.")
        self.news_tree = ttk.Treeview(parent, columns=cols, show="headings", height=12)
        for col, head in zip(cols, headings):
            self.news_tree.heading(col, text=head)
            self.news_tree.column(col, width=100, anchor="center")
        self.news_tree.column("event", width=220, anchor="w")
        self.news_tree.pack(fill="both", expand=True, padx=10, pady=6)

    def save_news_settings(self):
        provider = self.news_provider_combo.get()
        api_key = self.news_api_key_var.get().strip()
        keys = dict(getattr(cfg, "NEWS_API_KEYS", {}) or {})
        # Ключ новостного API тоже шифруется паролем входа перед записью на
        # диск (см. secure_store.py) — как и пароль MT5.
        pw = control.get_session_password()
        salt = getattr(cfg, "SECURITY_SALT", "")
        keys[provider] = secure_store.encrypt_value(api_key, pw, salt) if (pw and salt) else api_key
        _write_config_value("NEWS_API_PROVIDER", repr(provider))
        _write_config_value("NEWS_API_KEYS", repr(keys))
        try:
            _reload_cfg()
        except Exception:
            pass
        messagebox.showinfo(APP_TITLE, "Настройки новостей сохранены.")
        self.refresh_news_tab()

    def refresh_news_tab(self):
        self.news_status_var.set("Загружаю...")
        threading.Thread(target=self._refresh_news_worker, daemon=True).start()

    def _refresh_news_worker(self):
        events, error = news_calendar.upcoming_events(days_ahead=3, min_impact="medium")
        self.root.after(0, lambda: self._apply_news_result(events, error))

    def _apply_news_result(self, events, error):
        for item in self.news_tree.get_children():
            self.news_tree.delete(item)
        for e in events:
            self.news_tree.insert("", "end", values=(
                e["time"].strftime("%d.%m %H:%M"), e["currency"], e["event"], e["impact"],
                e.get("actual", ""), e.get("estimate", ""), e.get("prev", ""),
            ))
        if error:
            self.news_status_var.set(error)
        elif not events:
            self.news_status_var.set("Нет предстоящих новостей в ближайшие дни (или ключ ещё не настроен).")
        else:
            self.news_status_var.set(f"Событий: {len(events)}")

    # ---- вкладка "Chat AI" ------------------------------------------------------------
    def _build_tab_chat(self, parent):
        ttk.Label(parent, text="Чат с Claude", font=("Segoe UI", 12, "bold")).pack(padx=10, pady=6, anchor="w")

        text_frame = ttk.Frame(parent)
        text_frame.pack(fill="both", expand=True, padx=10, pady=6)
        self.chat_text = tk.Text(text_frame, wrap="word", state="disabled", bg="#242424", fg="#eee",
                                  insertbackground="#eee")
        self.chat_text.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(text_frame, command=self.chat_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.chat_text.config(yscrollcommand=scrollbar.set)

        input_frame = ttk.Frame(parent)
        input_frame.pack(fill="x", padx=10, pady=6)
        self.chat_entry = ttk.Entry(input_frame)
        self.chat_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.chat_entry.bind("<Return>", lambda e: self.send_chat_message())
        self.chat_send_btn = ttk.Button(input_frame, text="Отправить", command=self.send_chat_message)
        self.chat_send_btn.pack(side="left")

    def _append_chat(self, who: str, text: str):
        self.chat_text.config(state="normal")
        self.chat_text.insert("end", f"{who}: {text}\n\n")
        self.chat_text.see("end")
        self.chat_text.config(state="disabled")

    def send_chat_message(self):
        msg = self.chat_entry.get().strip()
        if not msg:
            return
        self.chat_entry.delete(0, "end")
        self._append_chat("Ты", msg)
        self.chat_history.append({"role": "user", "content": msg})
        self.chat_send_btn.config(state="disabled")
        threading.Thread(target=self._chat_worker, daemon=True).start()

    def _chat_worker(self):
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                messages=self.chat_history[-20:],
            )
            answer = resp.content[0].text
        except Exception as e:
            answer = f"[Ошибка обращения к Claude: {e}]"
        self.chat_history.append({"role": "assistant", "content": answer})
        self.root.after(0, lambda: self._append_chat("Claude", answer))
        self.root.after(0, lambda: self.chat_send_btn.config(state="normal"))

    # ---- вкладка "Как пользоваться" (всегда видна) --------------------------------
    def _build_tab_help(self, parent):
        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True, padx=6, pady=6)
        text = tk.Text(outer, wrap="word", bg="#1b1b1b", fg="#eee", insertbackground="#eee",
                        relief="flat", padx=14, pady=10, font=("Segoe UI", 10))
        scrollbar = ttk.Scrollbar(outer, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        text.tag_configure("h1", font=("Segoe UI", 13, "bold"), foreground="#4caf50", spacing3=6)
        text.tag_configure("h2", font=("Segoe UI", 11, "bold"), foreground="#e0e0e0", spacing1=10, spacing3=4)
        text.tag_configure("body", font=("Segoe UI", 10), foreground="#cfcfcf", spacing3=4)

        def h1(s):
            text.insert("end", s + "\n", "h1")

        def h2(s):
            text.insert("end", s + "\n", "h2")

        def p(s):
            text.insert("end", s + "\n", "body")

        h1("AI Scalper Pro — как пользоваться программой")
        p("Подробная инструкция по всем вкладкам и функциям, по порядку.")

        h2("1. Экран входа")
        p("Логин и пароль — те же, что заданы в настройках (DASHBOARD_LOGIN/пароль). "
          "Галочка «Запомнить пароль» — сохраняет пароль на ЭТОМ компьютере (Windows "
          "сам защищает файл, привязка к учётной записи Windows). При следующем "
          "запуске пароль будет уже подставлен в поле — просто нажми «Войти». Число "
          "попыток входа не ограничено.")

        h2("2. Обзор")
        p("Главная вкладка. Переключатель «Простой/Продвинутый» — простой режим "
          "показывает только самое нужное (Обзор/Символы/Сделки/Лог/Настройка/Как "
          "пользоваться), продвинутый добавляет ещё Брокер/Equity/Новости/Chat AI. "
          "Кнопки «Старт»/«Стоп» управляют торговым циклом вручную (хотя он и "
          "так запускается сам сразу при входе). «Полный выход» — гарантированно "
          "закрывает программу целиком (не путать с обычным закрытием окна, которое "
          "просто сворачивает программу в трей). Здесь же видно баланс, эквити, "
          "профиль риска, режим торговли и краткую статистику.")

        h2("3. Брокер (продвинутый режим)")
        p("Данные подключения к MT5: сервер/логин/пароль твоего брокера. Если "
          "оставить поля пустыми — программа подключается к уже открытому и "
          "залогиненному терминалу MT5. Если заполнить — программа сама запускает "
          "терминал и логинится этими данными при каждом старте. Кнопка «Проверить "
          "подключение» временно недоступна, пока бот работает (чтобы не дёргать MT5 "
          "из двух мест одновременно).")

        h2("4. Символы")
        p("Список торгуемых инструментов. Можно добавлять/удалять пары прямо здесь, "
          "без правки файлов. Двойной клик по названию символа открывает мини-график "
          "цены. Видно score BUY/SELL, режим рынка (тренд/флэт), сигнал AI, статус "
          "автообучения и причину, по которой последний раз не открылась сделка.")

        h2("5. Сделки")
        p("Все ОТКРЫТЫЕ позиции на счёте — не только те, что открыл этот бот, но и "
          "открытые вручную в терминале MT5 (колонка «Источник»: Бот/Ручная). Кнопка "
          "«Закрыть выбранную сделку» закрывает позицию по рынку — работает для любой "
          "строки в таблице.")

        h2("6. Лог")
        p("История сделок. Верхняя таблица — журнал этого бота (что и когда бот сам "
          "открывал/закрывал, с score). Нижняя таблица — синхронизированная история "
          "из MT5 (раздел «Синхронизация с MetaTrader»): подтягивается напрямую из "
          "брокера раз в минуту, включает вообще ВСЕ закрытые сделки за последние "
          "30 дней (в т.ч. открытые вручную), и статистика (винрейт, профит-фактор) "
          "там всегда 100% совпадает с историей у брокера.")

        h2("7. Equity (продвинутый режим)")
        p("График изменения эквити счёта с момента запуска программы.")

        h2("8. Настройка (видна всегда)")
        p("Сверху — быстрые переключатели: профиль риска (Консервативный/"
          "Сбалансированный/Агрессивный/Истеричка), режим торговли (Скальпинг/"
          "Новости/Оба), пауза новых сделок, звук+всплывающие уведомления.\n"
          "Ниже — ПОЛНЫЙ список входных параметров торговой логики, один в один "
          "как окно «Inputs» в MQL5-советнике: индикаторы, price action, объём, "
          "автонастройка под инструмент, защитные фильтры, AI-сигнал, режим/"
          "контекст рынка, score-фильтр, стопы/TP, Break Even, трейлинг, Profit "
          "Lock, просадка/серии убытков, издержки, новости, автообучение, "
          "автообновление, часы торговли, лот, частичное закрытие — плюс отдельный "
          "редактор каждого профиля риска и корреляций контекста рынка по символам. "
          "«Сохранить» применяет изменения сразу, без перезапуска программы.")

        h2("9. Новости (продвинутый режим)")
        p("Источник экономического календаря (провайдер + API-ключ) и таблица "
          "предстоящих новостей. Пока ключ не задан — фильтр по новостям просто "
          "не влияет на торговлю (безопасное поведение по умолчанию).")

        h2("10. Chat AI (продвинутый режим)")
        p("Обычный чат с Claude прямо в программе — можно спросить что угодно про "
          "рынок или настройки, не переключаясь на другое окно.")

        h2("Значок в трее и автозапуск")
        p("Закрытие окна крестиком сворачивает программу в трей, а не закрывает её — "
          "торговля продолжается в фоне. Из меню на значке в трее — Старт/Стоп/"
          "Показать окно/Полный выход. Галочка «Запускать вместе с Windows» "
          "(в «Обзоре») включает автозапуск при включении компьютера.")

        h2("Безопасность")
        p("Пароль MT5 и ключи AI/новостей хранятся в config.py в зашифрованном виде "
          "(не открытым текстом) — расшифровываются только в памяти после входа. "
          "config.py переписывается атомарно с резервными копиями, чтобы сбой "
          "посреди записи (антивирус, отключение питания) не испортил файл.")

        text.config(state="disabled")

    # ---- запуск/остановка торгового движка --------------------------------
    def start_bot(self):
        if self.bot_thread and self.bot_thread.is_alive():
            return
        self.stop_event = threading.Event()
        self.bot_thread = threading.Thread(target=self._run_bot, daemon=True)
        self.bot_thread.start()
        self.status_var.set("Запускается...")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

    def _run_bot(self):
        try:
            bot_engine.main(stop_event=self.stop_event, start_dashboard=False)
        except Exception as e:
            log.exception("Бот остановился с ошибкой: %s", e)
            self.status_var.set(f"Ошибка: {e}")
            messagebox.showerror(APP_TITLE, f"Бот остановился с ошибкой:\n{e}\n\nПодробности в scalper.log.")
        finally:
            if "Ошибка" not in self.status_var.get():
                self.status_var.set("Остановлен")
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")

    def stop_bot(self):
        if self.stop_event:
            self.stop_event.set()
        self.status_var.set("Останавливается...")
        self.stop_btn.config(state="disabled")

    # ---- периодическое обновление всех вкладок --------------------------------
    def _refresh_loop(self):
        try:
            snap = ds.get_snapshot()
            if snap:
                acc = snap.get("account", {})
                mode_txt = "LIVE" if snap.get("live_trading") else "DRY-RUN"
                self.info_var.set(
                    f"Счёт {acc.get('login', '-')}  ({acc.get('server', '-')})\n"
                    f"Баланс: {acc.get('balance', 0):.2f}   Эквити: {acc.get('equity', 0):.2f}\n"
                    f"Режим: {mode_txt} | Профиль: {snap.get('risk_profile', '-')} | "
                    f"Торговля: {snap.get('trading_mode', '-')} | Сделок сегодня: {snap.get('trades_today', 0)}"
                )
                st = snap.get("stats", {})
                self.stats_var.set(
                    f"Сделок всего: {st.get('total_trades', 0)}   Винрейт: {st.get('win_rate', 0)}%\n"
                    f"P/L за день: {st.get('day_pnl_pct', 0)}%   Просадка: {st.get('drawdown_pct', 0)}%   "
                    f"Открытых позиций: {len(snap.get('positions', []))}"
                )
                # Диагностика "сделки не открываются" — предупреждение видно, только
                # если реально есть проблема с разрешением на торговлю в MT5.
                perm = snap.get("trade_permission", {}) or {}
                problems = perm.get("problems", [])
                if problems:
                    self.trade_warning_var.set(
                        "⚠ Сделки могут не открываться:\n- " + "\n- ".join(problems)
                    )
                else:
                    self.trade_warning_var.set("")
            if self.bot_thread and self.bot_thread.is_alive():
                pause_txt = " (пауза)" if control.is_paused() else ""
                self.status_var.set("Работает" + pause_txt)

            self._refresh_symbols_tab()
            self._refresh_positions_tab()
            self._refresh_log_tab()
            self._redraw_equity_canvas()
            self.pause_btn.config(text="Возобновить торговлю" if control.is_paused() else "Пауза (новые сделки)")

            for title, message in control.drain_notifications():
                self._show_toast(title, message)
        except Exception:
            log.exception("Ошибка обновления интерфейса")
        self.root.after(3000, self._refresh_loop)

    # ---- уведомления --------------------------------------------------------------
    def _show_toast(self, title: str, message: str):
        try:
            if getattr(cfg, "USE_SOUND_NOTIFICATIONS", True) and sys.platform == "win32":
                import winsound
                winsound.MessageBeep()
        except Exception:
            pass
        try:
            toast = tk.Toplevel(self.root)
            toast.overrideredirect(True)
            toast.attributes("-topmost", True)
            toast.configure(bg="#2a2a2a")
            x = self.root.winfo_x() + max(self.root.winfo_width() - 320, 0)
            y = self.root.winfo_y() + 40
            toast.geometry(f"300x70+{max(x, 0)}+{max(y, 0)}")
            tk.Label(toast, text=title, bg="#2a2a2a", fg="#4caf50", font=("Segoe UI", 10, "bold"),
                     anchor="w").pack(fill="x", padx=10, pady=(8, 0))
            tk.Label(toast, text=message, bg="#2a2a2a", fg="#eee", anchor="w", wraplength=280,
                     justify="left").pack(fill="x", padx=10)
            toast.after(4500, toast.destroy)
        except Exception:
            pass

    # ---- экспорт в Excel ----------------------------------------------------------
    def export_excel(self):
        try:
            import openpyxl
        except ImportError:
            messagebox.showerror(APP_TITLE, "Библиотека openpyxl не установлена.\nВыполни: pip install openpyxl")
            return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")],
                                             initialfile="ai_scalper_report.xlsx")
        if not path:
            return
        try:
            wb = openpyxl.Workbook()
            ws_trades = wb.active
            ws_trades.title = "Сделки"
            if os.path.exists(cfg.LOG_CSV_PATH):
                with open(cfg.LOG_CSV_PATH, encoding="utf-8") as f:
                    for row in csv.reader(f, delimiter=";"):
                        ws_trades.append(row)

            ws_stats = wb.create_sheet("Статистика")
            snap = ds.get_snapshot() or {}
            st = snap.get("stats", {})
            ws_stats.append(["Показатель", "Значение"])
            for k, v in st.items():
                ws_stats.append([k, v])
            acc = snap.get("account", {})
            ws_stats.append([])
            ws_stats.append(["Счёт", acc.get("login", "-")])
            ws_stats.append(["Сервер", acc.get("server", "-")])
            ws_stats.append(["Баланс", acc.get("balance", 0)])
            ws_stats.append(["Эквити", acc.get("equity", 0)])

            # Синхронизация с MetaTrader (п.4): реальная история сделок брокера,
            # включая открытые вручную — не только журнал этого бота выше.
            ws_mt5 = wb.create_sheet("История MT5")
            ws_mt5.append(["Тикет", "Время", "Символ", "Тип", "Лот", "Цена", "Профит", "Источник"])
            for d in snap.get("mt5_history", []):
                ws_mt5.append([
                    d.get("ticket"), d.get("time"), d.get("symbol"), d.get("type"),
                    d.get("volume"), d.get("price"), d.get("profit"),
                    "Бот" if d.get("is_bot", True) else "Ручная",
                ])
            hist_stats = snap.get("mt5_history_stats", {})
            if hist_stats:
                ws_mt5.append([])
                for k, v in hist_stats.items():
                    ws_mt5.append([k, v])

            wb.save(path)
            messagebox.showinfo(APP_TITLE, f"Сохранено: {path}")
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Не удалось сохранить: {e}")

    # ---- вспомогательные действия ------------------------------------------
    def open_dashboard(self):
        webbrowser.open(f"http://127.0.0.1:{cfg.DASHBOARD_PORT}")

    def open_logs(self):
        try:
            os.startfile(_app_dir)
        except Exception:
            messagebox.showinfo("Логи", f"Файл лога: {LOG_FILE}")

    def open_config(self):
        config_path = os.path.join(_app_dir, "config.py")
        try:
            os.startfile(config_path)
        except Exception:
            messagebox.showinfo("Настройки", f"Файл настроек: {config_path}")

    def _toggle_autostart(self):
        if self.autostart_var.get():
            _enable_autostart()
        else:
            _disable_autostart()

    # ---- системный трей -----------------------------------------------------
    def _start_tray(self):
        image = Image.new("RGB", (64, 64), "#111111")
        d = ImageDraw.Draw(image)
        d.ellipse((6, 6, 58, 58), fill="#4caf50")
        d.text((20, 24), "AI", fill="white")

        menu = pystray.Menu(
            pystray.MenuItem("Показать окно", self._show_window, default=True),
            pystray.MenuItem("Старт", lambda: self.root.after(0, self.start_bot)),
            pystray.MenuItem("Стоп", lambda: self.root.after(0, self.stop_bot)),
            pystray.MenuItem("Полный выход", lambda: self.root.after(0, self._hard_quit)),
        )
        self.tray_icon = pystray.Icon(APP_TITLE, image, APP_TITLE, menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _show_window(self, *_):
        self.root.after(0, self.root.deiconify)

    def _on_close(self):
        if TRAY_AVAILABLE:
            self.root.withdraw()
        else:
            if messagebox.askyesno(APP_TITLE, "Закрыть программу? Бот (если запущен) будет остановлен."):
                self._hard_quit()

    def full_exit(self):
        if messagebox.askyesno(APP_TITLE, "Полностью закрыть программу? Бот (если запущен) будет остановлен, "
                                            "процесс исчезнет из Диспетчера задач."):
            self._hard_quit()

    def _hard_quit(self, *_):
        try:
            if self.stop_event:
                self.stop_event.set()
        except Exception:
            pass
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)  # гарантированное завершение процесса — ничего не остаётся в Диспетчере задач

    def run(self):
        self.root.mainloop()


# =====================================================================
# Автозапуск с Windows (реестр HKCU\...\Run — не требует прав администратора)
# =====================================================================
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_REG_NAME = "AIScalperPro"


def _exe_path() -> str:
    return sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)


def _is_autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _APP_REG_NAME)
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _enable_autostart():
    if sys.platform != "win32":
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _APP_REG_NAME, 0, winreg.REG_SZ, f'"{_exe_path()}"')
    except Exception as e:
        log.warning("Не удалось включить автозапуск: %s", e)


def _disable_autostart():
    if sys.platform != "win32":
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _APP_REG_NAME)
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("Не удалось выключить автозапуск: %s", e)


def _show_login() -> bool:
    """Экран входа ПЕРЕД открытием программы — тот же логин/пароль, что и у
    веб-дашборда (cfg.DASHBOARD_LOGIN / DASHBOARD_PASSWORD, задаются в config.py
    или на вкладке дашборда). Пока не введён верный логин/пароль — окно
    программы не открывается."""
    login_root = tk.Tk()
    login_root.title(APP_TITLE)
    login_root.geometry("340x260")
    login_root.resizable(False, False)
    login_root.configure(bg="#1b1b1b")
    try:
        style = ttk.Style(login_root)
        style.theme_use("clam")
        style.configure(".", background="#1b1b1b", foreground="#eee", fieldbackground="#242424")
    except tk.TclError:
        pass

    ok_holder = {"ok": False}

    ttk.Label(login_root, text=APP_TITLE, font=("Segoe UI", 14, "bold")).pack(pady=(18, 10))

    form = ttk.Frame(login_root)
    form.pack(padx=30, fill="x")
    ttk.Label(form, text="Логин:").pack(anchor="w")
    login_var = tk.StringVar()
    login_entry = ttk.Entry(form, textvariable=login_var, width=28)
    login_entry.pack(fill="x")

    ttk.Label(form, text="Пароль:").pack(anchor="w", pady=(8, 0))
    pass_var = tk.StringVar()
    pass_entry = ttk.Entry(form, textvariable=pass_var, width=28, show="*")
    pass_entry.pack(fill="x")

    remembered = _load_remembered_password()
    if remembered:
        pass_var.set(remembered)
    remember_var = tk.BooleanVar(value=bool(remembered))
    ttk.Checkbutton(login_root, text="Запомнить пароль", variable=remember_var).pack(pady=(8, 0))

    status_var = tk.StringVar(value="")
    ttk.Label(login_root, textvariable=status_var, foreground="#e57373").pack(pady=(8, 0))

    def try_login(*_):
        entered_login = login_var.get()
        entered_password = pass_var.get()
        stored_hash = getattr(cfg, "DASHBOARD_PASSWORD_HASH", "")
        if stored_hash:
            salt = getattr(cfg, "SECURITY_SALT", "")
            ok_login = entered_login == str(getattr(cfg, "DASHBOARD_LOGIN", ""))
            ok_pass = secure_store.verify_password(entered_password, salt, stored_hash)
        else:
            # Легаси-формат (миграция ещё не прошла) — сравнение как раньше.
            ok_login = entered_login == str(getattr(cfg, "DASHBOARD_LOGIN", ""))
            ok_pass = entered_password == str(getattr(cfg, "DASHBOARD_PASSWORD", ""))

        if not (ok_login and ok_pass):
            status_var.set("Неверный логин или пароль.")
            pass_var.set("")
            return

        try:
            secure_store.unlock_config(cfg, entered_password)
        except ValueError as e:
            status_var.set(str(e))
            pass_var.set("")
            return

        control.set_session_password(entered_password)
        if remember_var.get():
            _save_remembered_password(entered_password)
        else:
            _clear_remembered_password()
        ok_holder["ok"] = True
        login_root.destroy()

    ttk.Button(login_root, text="Войти", command=try_login).pack(pady=14)
    login_entry.bind("<Return>", lambda e: pass_entry.focus_set())
    pass_entry.bind("<Return>", try_login)
    login_root.protocol("WM_DELETE_WINDOW", login_root.destroy)
    login_entry.focus_set()
    login_root.mainloop()
    return ok_holder["ok"]


def main():
    _migrate_legacy_secrets()
    _harden_files()
    if not _show_login():
        return
    app = App()
    app.run()


if __name__ == "__main__":
    main()
