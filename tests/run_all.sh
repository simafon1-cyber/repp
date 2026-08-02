#!/usr/bin/env bash
# Запуск всех проверок проекта.
# Использование:  bash tests/run_all.sh
set -u

cd "$(dirname "$0")"
FAILED=0

echo "==================================================="
echo " 1/7  Статическая проверка DualGuard EA (MQL5)"
echo "==================================================="
python3 lint_mq5.py || FAILED=1

echo
echo "==================================================="
echo " 2/7  Статическая проверка AI Scalper Pro (MQL5)"
echo "==================================================="
python3 lint_mq5.py ../ai_scalper_pro/AI_Scalper_Pro.mq5 ../ai_scalper_pro/*.mqh || FAILED=1

echo
echo "==================================================="
echo " 3/7  Тесты логики DualGuard EA (C++)"
echo "==================================================="
if python3 extract_functions.py && g++ -std=c++17 -Wall -o test_logic test_logic.cpp; then
    ./test_logic || FAILED=1
else
    echo "ОШИБКА: не удалось собрать тесты"
    FAILED=1
fi

echo
echo "==================================================="
echo " 4/7  Тесты расчёта риска AI Scalper Pro (C++)"
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
echo " 5/7  Тесты моста DualGuard (Python + Claude)"
echo "==================================================="
python3 test_bridge.py || FAILED=1

echo
echo "==================================================="
echo " 6/7  Тесты моста AI Scalper Pro (Python)"
echo "==================================================="
python3 test_scalper_bridge.py || FAILED=1

echo
echo "==================================================="
echo " 7/7  Тесты программы Trader (счета, пароли, процессы)"
echo "==================================================="
python3 test_trader_app.py || FAILED=1

echo
if [ "$FAILED" -eq 0 ]; then
    echo "ИТОГ: все проверки пройдены."
else
    echo "ИТОГ: есть провалившиеся проверки (см. выше)."
fi
exit "$FAILED"
