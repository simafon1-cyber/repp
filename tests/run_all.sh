#!/usr/bin/env bash
# Запуск всех проверок проекта DualGuard EA.
# Использование:  bash tests/run_all.sh
set -u

cd "$(dirname "$0")"
FAILED=0

echo "==================================================="
echo " 1/3  Статическая проверка советника MQL5"
echo "==================================================="
python3 lint_mq5.py || FAILED=1

echo
echo "==================================================="
echo " 2/3  Тесты логики советника (C++)"
echo "==================================================="
if python3 extract_functions.py && g++ -std=c++17 -Wall -o test_logic test_logic.cpp; then
    ./test_logic || FAILED=1
else
    echo "ОШИБКА: не удалось собрать тесты"
    FAILED=1
fi

echo
echo "==================================================="
echo " 3/3  Тесты Python-моста"
echo "==================================================="
python3 test_bridge.py || FAILED=1

echo
if [ "$FAILED" -eq 0 ]; then
    echo "ИТОГ: все проверки пройдены."
else
    echo "ИТОГ: есть провалившиеся проверки (см. выше)."
fi
exit "$FAILED"
