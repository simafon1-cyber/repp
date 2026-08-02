"""
list_symbols.py — разовый диагностический скрипт: подключается к уже открытому
MT5 и печатает все символы у брокера, содержащие ключевые слова (золото,
евро, фунт, биткоин и т.п.). Нужен, чтобы найти ТОЧНЫЕ названия для SYMBOLS
в config.py — у многих брокеров они с суффиксами (.m, .raw, .iux и т.д.).

Запуск: python list_symbols.py
"""

import MetaTrader5 as mt5

KEYWORDS = ["XAU", "GOLD", "EUR", "GBP", "BTC", "USD"]

if not mt5.initialize():
    print("Не удалось подключиться к MT5:", mt5.last_error())
    raise SystemExit(1)

acc = mt5.account_info()
print(f"Подключено к MT5. Счёт {acc.login} ({acc.server})\n")

all_symbols = mt5.symbols_get()
print(f"Всего символов у брокера: {len(all_symbols)}\n")

matches = [s.name for s in all_symbols if any(k in s.name.upper() for k in KEYWORDS)]
matches.sort()

print("Символы, похожие на нужные (золото/евро/фунт/биткоин):")
for name in matches:
    print(" ", name)

if not matches:
    print("  (ничего не найдено — вот ПОЛНЫЙ список первых 50 символов брокера)")
    for s in all_symbols[:50]:
        print(" ", s.name)

mt5.shutdown()
