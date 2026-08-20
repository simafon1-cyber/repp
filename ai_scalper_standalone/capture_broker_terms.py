"""capture_broker_terms.py — ВЫГРУЗИТЬ НАСТОЯЩИЕ УСЛОВИЯ СЧЁТА.

ЗАЧЕМ ЭТОТ СКРИПТ

В модели издержек сейчас стоят ТИПОВЫЕ значения розничного счёта:
комиссия, свопы, размеры контракта. Это допущения, а не условия вашего
счёта. Внешний аудит потребовал не выдавать одно за другое.

Этот скрипт выгружает настоящие условия из терминала — там они есть по
каждому инструменту. После этого исследование можно пересчитать на
фактических числах, а не на предположениях.

ГДЕ ЕГО ЗАПУСКАТЬ

На вашем компьютере, где установлен MetaTrader 5 и открыт терминал. В
облаке, где считалось исследование, терминала нет — поэтому выгрузку
сделать оттуда невозможно, и это честно указано в отчёте.

    python capture_broker_terms.py

Скрипт ничего не меняет и не торгует: только читает справочник
инструментов. Пароль не требуется — терминал уже открыт.

ЧЕГО ОН НЕ МОЖЕТ

Терминал показывает условия НА СЕГОДНЯ, а не за прошлый период. Если
брокер менял комиссию или свопы в течение исследуемых месяцев, восстановить
это уже нельзя. Поэтому в выгрузку записывается дата, и в отчёте она
называется прямо: «условия на такое-то число», а не «условия за период».
"""

import json
import os
import sys
from datetime import datetime, timezone

ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))
ФАЙЛ = os.path.join(ЗДЕСЬ, "research", "broker_terms.json")

СИМВОЛЫ = ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD",
           "XAUUSD")


def собрать(symbol, mt5) -> dict:
    """Всё, что терминал знает об инструменте и что влияет на издержки."""
    info = mt5.symbol_info(symbol)
    if info is None:
        if not mt5.symbol_select(symbol, True):
            return {"инструмент": symbol, "ошибка": "инструмент недоступен"}
        info = mt5.symbol_info(symbol)
    if info is None:
        return {"инструмент": symbol, "ошибка": "справочник не читается"}

    тик = mt5.symbol_info_tick(symbol)
    return {
        "инструмент": symbol,
        "описание": getattr(info, "description", ""),
        "размер_контракта": getattr(info, "trade_contract_size", None),
        "пункт": getattr(info, "point", None),
        "знаков_после_запятой": getattr(info, "digits", None),
        "стоимость_тика": getattr(info, "trade_tick_value", None),
        "размер_тика": getattr(info, "trade_tick_size", None),
        "спред_сейчас_пунктов": getattr(info, "spread", None),
        "спред_плавающий": bool(getattr(info, "spread_float", False)),
        "своп_покупка": getattr(info, "swap_long", None),
        "своп_продажа": getattr(info, "swap_short", None),
        "режим_свопа": getattr(info, "swap_mode", None),
        "день_тройного_свопа": getattr(info, "swap_rollover3days", None),
        "минимальный_лот": getattr(info, "volume_min", None),
        "шаг_лота": getattr(info, "volume_step", None),
        "минимальная_дистанция_стопа": getattr(info, "trade_stops_level", None),
        "валюта_прибыли": getattr(info, "currency_profit", ""),
        "цена_на_момент_выгрузки": (getattr(тик, "bid", None)
                                    if тик is not None else None),
    }


def main() -> int:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("Модуля MetaTrader5 нет в этой системе.")
        print("Скрипт нужно запускать на компьютере с установленным")
        print("терминалом MetaTrader 5, а не в облаке.")
        return 2

    if not mt5.initialize():
        print("Терминал не отвечает:", mt5.last_error())
        print("Откройте MetaTrader 5 и повторите.")
        return 2

    try:
        счёт = mt5.account_info()
        выгрузка = {
            "выгружено": datetime.now(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"),
            "ВНИМАНИЕ": ("это условия НА МОМЕНТ ВЫГРУЗКИ, а не за прошлый "
                         "период. Если брокер менял комиссию или свопы, "
                         "восстановить это уже нельзя."),
            "счёт": {
                "номер": getattr(счёт, "login", None),
                "сервер": getattr(счёт, "server", ""),
                "валюта": getattr(счёт, "currency", ""),
                "плечо": getattr(счёт, "leverage", None),
                "тип_маржи": getattr(счёт, "margin_mode", None),
                "торговый_режим": getattr(счёт, "trade_mode", None),
            },
            "КОМИССИЯ": ("терминал НЕ сообщает комиссию через API — её надо "
                         "взять из спецификации счёта у брокера и вписать "
                         "сюда руками, в поле «комиссия_за_круг_на_лот»"),
            "комиссия_за_круг_на_лот": None,
            "инструменты": [собрать(s, mt5) for s in СИМВОЛЫ],
        }
    finally:
        mt5.shutdown()

    os.makedirs(os.path.dirname(ФАЙЛ), exist_ok=True)
    with open(ФАЙЛ, "w", encoding="utf-8") as f:
        json.dump(выгрузка, f, ensure_ascii=False, indent=2)

    print(f"Записано: {ФАЙЛ}")
    print()
    print("ЧТО ДАЛЬШЕ:")
    print("  1. Откройте файл и впишите комиссию за круг на лот —")
    print("     её терминал не отдаёт, она есть в условиях счёта у брокера.")
    print("  2. Пришлите файл: исследование пересчитается на фактических")
    print("     числах вместо типовых допущений.")
    print()
    для_справки = [и for и in выгрузка["инструменты"] if "ошибка" not in и]
    if для_справки:
        print("Что выгружено (для беглой проверки):")
        print(f"  {'инструмент':<10}{'контракт':>12}{'своп buy':>10}"
              f"{'своп sell':>11}{'спред':>7}")
        for и in для_справки:
            print(f"  {и['инструмент']:<10}{и['размер_контракта'] or 0:>12.0f}"
                  f"{и['своп_покупка'] or 0:>10.2f}{и['своп_продажа'] or 0:>11.2f}"
                  f"{и['спред_сейчас_пунктов'] or 0:>7}")
    плохие = [и["инструмент"] for и in выгрузка["инструменты"] if "ошибка" in и]
    if плохие:
        print(f"\n  НЕ ВЫГРУЖЕНЫ: {', '.join(плохие)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
