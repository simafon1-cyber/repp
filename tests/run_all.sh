#!/usr/bin/env bash
# Запуск всех проверок проекта.
# Использование:  bash tests/run_all.sh
set -u

cd "$(dirname "$0")"
FAILED=0

echo "==================================================="
echo " 1/14  Статическая проверка DualGuard EA (MQL5)"
echo "==================================================="
python3 lint_mq5.py || FAILED=1
python3 lint_mq5.py ../mql5/CalendarExport.mq5 || FAILED=1

echo
echo "==================================================="
echo " 2/14  Статическая проверка AI Scalper Pro (MQL5)"
echo "==================================================="
python3 lint_mq5.py ../ai_scalper_pro/AI_Scalper_Pro.mq5 ../ai_scalper_pro/*.mqh || FAILED=1

echo
echo "==================================================="
echo " 3/14  Тесты логики DualGuard EA (C++)"
echo "==================================================="
if python3 extract_functions.py && g++ -std=c++17 -Wall -o test_logic test_logic.cpp; then
    ./test_logic || FAILED=1
else
    echo "ОШИБКА: не удалось собрать тесты"
    FAILED=1
fi

echo
echo "==================================================="
echo " 4/14  Тесты расчёта риска AI Scalper Pro (C++)"
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
echo " 5/14  Тесты моста DualGuard (Python + Claude)"
echo "==================================================="
python3 test_bridge.py || FAILED=1

echo
echo "==================================================="
echo " 6/14  Тесты моста AI Scalper Pro (Python)"
echo "==================================================="
python3 test_scalper_bridge.py || FAILED=1

echo
echo "==================================================="
echo " 7/14  Тесты многосчётности (счета, шифрование, процессы)"
echo "==================================================="
python3 test_multi_account.py || FAILED=1

echo
echo "==================================================="
echo " 8/14  Тесты готовых стратегий"
echo "==================================================="
python3 test_strategies.py || FAILED=1

echo
echo "==================================================="
echo " 9/14  Тесты фиксации прибыли (тейк-профит, безубыток, обучение цели)"
echo "==================================================="
python3 test_profit_taking.py || FAILED=1

echo
echo "==================================================="
echo "10/14  Тесты источников новостей (календарь MT5, цепочка, график)"
echo "==================================================="
python3 test_news_sources.py || FAILED=1

echo
echo "==================================================="
echo "11/14  Тесты расписания работы бота (вкладка «Календарь»)"
echo "==================================================="
python3 test_schedule.py || FAILED=1

echo
echo "==================================================="
echo "12/14  Тесты сигналов из Telegram (границы полномочий)"
echo "==================================================="
python3 test_telegram.py || FAILED=1

echo
echo "==================================================="
echo "13/14  Тесты приватного режима (что открывается, что нет)"
echo "==================================================="
python3 test_private_mode.py || FAILED=1

echo
echo "==================================================="
echo "14/14  Тесты установки в MetaTrader (всё ставится само)"
echo "==================================================="
python3 test_mt5_install.py || FAILED=1

echo
if [ "$FAILED" -eq 0 ]; then
    echo "ИТОГ: все проверки пройдены."
else
    echo "ИТОГ: есть провалившиеся проверки (см. выше)."
fi
exit "$FAILED"
