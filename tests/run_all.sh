#!/usr/bin/env bash
# Запуск всех проверок проекта.
# Использование:  bash tests/run_all.sh
set -u

cd "$(dirname "$0")"
FAILED=0

echo "==================================================="
echo " 1/25  Статическая проверка DualGuard EA (MQL5)"
echo "==================================================="
python3 lint_mq5.py || FAILED=1
python3 lint_mq5.py ../mql5/CalendarExport.mq5 || FAILED=1

echo
echo "==================================================="
echo " 2/25  Статическая проверка AI Scalper Pro (MQL5)"
echo "==================================================="
python3 lint_mq5.py ../ai_scalper_pro/AI_Scalper_Pro.mq5 ../ai_scalper_pro/*.mqh || FAILED=1

echo
echo "==================================================="
echo " 3/25  Тесты логики DualGuard EA (C++)"
echo "==================================================="
if python3 extract_functions.py && g++ -std=c++17 -Wall -o test_logic test_logic.cpp; then
    ./test_logic || FAILED=1
else
    echo "ОШИБКА: не удалось собрать тесты"
    FAILED=1
fi

echo
echo "==================================================="
echo " 4/25  Тесты расчёта риска AI Scalper Pro (C++)"
echo "==================================================="
if python3 extract_functions.py ../ai_scalper_pro/RiskManager.mqh generated_scalper.h \
        VolumeDigitsOf FloorVolumeToStep GetLossStreakRiskMultiplier CalcLot \
        StateDaySerial StateGVPrefix StateGVInstance StateGVTradesName StateGVGet \
        SaveDailyState SaveRiskStreakState LoadRiskStreakState StartNewDayState \
        LoadDailyState DailyLossLimitHit MaxDrawdownHit LossStreakPauseActive \
   && g++ -std=c++17 -Wall -o test_ai_scalper test_ai_scalper.cpp; then
    ./test_ai_scalper || FAILED=1
else
    echo "ОШИБКА: не удалось собрать тесты"
    FAILED=1
fi

echo
echo "==================================================="
echo " 5/25  Тесты моста DualGuard (Python + Claude)"
echo "==================================================="
python3 test_bridge.py || FAILED=1

echo
echo "==================================================="
echo " 6/25  Тесты моста AI Scalper Pro (Python)"
echo "==================================================="
python3 test_scalper_bridge.py || FAILED=1

echo
echo "==================================================="
echo " 7/25  Тесты многосчётности (счета, шифрование, процессы)"
echo "==================================================="
python3 test_multi_account.py || FAILED=1

echo
echo "==================================================="
echo " 8/25  Тесты готовых стратегий"
echo "==================================================="
python3 test_strategies.py || FAILED=1

echo
echo "==================================================="
echo " 9/25  Тесты фиксации прибыли (тейк-профит, безубыток, обучение цели)"
echo "==================================================="
python3 test_profit_taking.py || FAILED=1

echo
echo "==================================================="
echo "10/25  Тесты источников новостей (календарь MT5, цепочка, график)"
echo "==================================================="
python3 test_news_sources.py || FAILED=1

echo
echo "==================================================="
echo "10б/25  Тесты самоналадки источника новостей"
echo "==================================================="
python3 test_news_autostart.py || FAILED=1

echo
echo "==================================================="
echo "10в/25  Тесты живучести торгового цикла (не умирает молча)"
echo "==================================================="
python3 test_bot_alive.py || FAILED=1

echo
echo "==================================================="
echo "10г/25  Тесты оформления окна (читаемость, контраст)"
echo "==================================================="
python3 test_ui_theme.py || FAILED=1

echo
echo "==================================================="
echo "10е/25  Тесты раскладки окна (управление, сохранение, прокрутка)"
echo "==================================================="
python3 test_window_layout.py || FAILED=1

echo
echo "==================================================="
echo "10д/25  Тесты: почему нет сделок, настройки, автовход"
echo "==================================================="
python3 test_silence_and_settings.py || FAILED=1

echo
echo "==================================================="
echo "10ж/25  Тесты трейлинга по риску сделки (R) и поджима тейка"
echo "==================================================="
python3 test_r_trail_ladder.py || FAILED=1

echo
echo "==================================================="
echo "10з/25  Тесты «рынок закрыт или неликвиден» (без часовых поясов)"
echo "==================================================="
python3 test_market_hours.py || FAILED=1

echo
echo "==================================================="
echo "11/25  Тесты расписания работы бота (вкладка «Календарь»)"
echo "==================================================="
python3 test_schedule.py || FAILED=1

echo
echo "==================================================="
echo "12/25  Тесты сигналов из Telegram (границы полномочий)"
echo "==================================================="
python3 test_telegram.py || FAILED=1

echo
echo "==================================================="
echo "13/25  Тесты приватного режима (что открывается, что нет)"
echo "==================================================="
python3 test_private_mode.py || FAILED=1

echo
echo "==================================================="
echo "14/25  Тесты установки в MetaTrader (всё ставится само)"
echo "==================================================="
python3 test_mt5_install.py || FAILED=1

echo
echo "==================================================="
echo "15/25  Тесты системы (встроенный мост, проверки, обновление)"
echo "==================================================="
python3 test_system.py || FAILED=1

echo
echo "==================================================="
echo "16/25  Тесты работы всю сессию (лимита сделок нет)"
echo "==================================================="
python3 test_no_trade_cap.py || FAILED=1

echo
echo "==================================================="
echo "17/25  Тесты настроек, порога убытка, календаря и журнала в облаке"
echo "==================================================="
python3 test_journal_and_config.py || FAILED=1

echo
echo "==================================================="
echo "18/25  Тесты самообновления (программа ставит новую версию сама)"
echo "==================================================="
python3 test_selfupdate.py || FAILED=1

echo
if [ "$FAILED" -eq 0 ]; then
    echo "ИТОГ: все проверки пройдены."
else
    echo "ИТОГ: есть провалившиеся проверки (см. выше)."
fi
exit "$FAILED"
