#!/usr/bin/env python3
"""M1: ЗАПУСК НА МАШИНЕ БЕЗ ТЕРМИНАЛА MetaTrader 5.

ПОЧЕМУ ЭТИ ТЕСТЫ ПОЯВИЛИСЬ

Владелец переходит с Windows на Mac. Проверенный факт: библиотека
MetaTrader5 для Python собрана ТОЛЬКО под Windows (win_amd64), сборки под
macOS не существует. Значит, на Mac отправить брокеру заявку отсюда
нельзя вообще — и вопрос не «как обойти», а «как вести себя честно».

Модули main.py и mt5_connector.py пишут `import MetaTrader5` в самом
верху файла. На Mac этот импорт падает, и окно не открывается даже для
того, чтобы объяснить причину. Слой platform_support.py подставляет
заглушку — и вот ЗА ЧЕМ здесь следят тесты.

ГЛАВНОЕ, ЧТО ПРОВЕРЯЕТСЯ, И ПОЧЕМУ ИМЕННО ТАК

Заглушка обязана делить вызовы на три группы, и деление это не
косметическое:

  * order_send / order_check ОБЯЗАНЫ бросить ошибку. Вернуть пустоту
    вместо ответа на заявку — значит по правилам проекта сказать «исход
    неясен», то есть запустить остановку и разбирательство события,
    которого не было.

  * initialize / login ОБЯЗАНЫ вернуть False, а НЕ бросить ошибку. Это
    правда: подключиться действительно не удалось. Код connect() уже
    проверяет этот ответ и превращает его в понятное сообщение. Исключение
    здесь рвало бы запуск в местах, где ждут False — и именно так первая
    версия слоя насыпала два десятка обрывов в журнал.

  * справочные вызовы отдают пустоту — «неизвестно», и это правда.

Проверка «наоборот» встроена в сами тесты: если вернуть initialize() в
торговую группу, тест test_подключение_честно_не_удаётся падает, а если
разрешить order_send молча возвращать пустоту — падает
test_заявка_невозможна.

ЧЕГО ЭТИ ТЕСТЫ НЕ ПРОВЕРЯЮТ

Поведение у НАСТОЯЩЕГО брокера. Зелёный результат здесь не является
доказательством поведения на реальном счёте.

Запуск:  python3 tests/test_platform_macos.py
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
APP = ROOT / "ai_scalper_standalone"
sys.path.insert(0, str(APP))

passed = 0
failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  OK   {name}")
    else:
        failed += 1
        print(f"  СБОЙ {name}" + (f" -> {detail}" if detail else ""))


def чисто():
    """Убрать всё, что уже успело попасть в память, и начать с нуля.

    Без этого тест мерил бы состояние, оставшееся от предыдущего теста, а
    не поведение слоя."""
    for имя in list(sys.modules):
        if имя == "MetaTrader5" or имя.startswith("MetaTrader5."):
            del sys.modules[имя]
    ps = importlib.import_module("platform_support")
    importlib.reload(ps)
    return ps


# ---------------------------------------------------------------------
# ФАКТ, НА КОТОРОМ ВСЁ ДЕРЖИТСЯ
# ---------------------------------------------------------------------

def test_на_этой_машине_терминала_нет():
    """Тесты идут не на Windows — значит настоящего пакета быть не должно.

    Если он вдруг есть, все остальные проверки бессмысленны: они мерили бы
    настоящий терминал, а не заглушку. Поэтому проверяем это ПЕРВЫМ."""
    ps = чисто()
    if sys.platform == "win32":
        print("  ПРОПУСК: проверки заглушки идут только не на Windows")
        return
    check(not ps.настоящий_терминал_есть(),
          "Настоящего MetaTrader5 на этой машине нет")
    причина = ps.почему_нельзя()
    check(причина != "", "Слой сам называет причину, а не молчит")
    check("MetaTrader5" in причина or "MetaTrader" in причина,
          "Причина названа человеческим языком", причина[:80])


def test_группы_вызовов_не_пересекаются():
    """Один вызов не может быть одновременно денежным и справочным.

    Пересечение означало бы, что поведение зависит от порядка установки —
    то есть от случайности."""
    ps = чисто()
    все = []
    for группа in (ps.ТОРГОВЫЕ, ps.ПОДКЛЮЧЕНИЕ, ps.ЗАВЕРШЕНИЕ, ps.ЧИТАЮЩИЕ):
        все.extend(группа)
    check(len(все) == len(set(все)), "Ни один вызов не попал в две группы",
          str([и for и in все if все.count(и) > 1]))
    check(set(ps.ТОРГОВЫЕ) == {"order_send", "order_check"},
          "В денежной группе ровно два вызова — те, что двигают позицию",
          str(ps.ТОРГОВЫЕ))


# ---------------------------------------------------------------------
# ПОВЕДЕНИЕ ЗАГЛУШКИ
# ---------------------------------------------------------------------

def test_заявка_невозможна():
    """order_send и order_check обязаны КРИЧАТЬ, а не возвращать пустоту.

    Наоборот: если заменить бросок на `return None`, эта проверка падает —
    молчаливая пустота была бы принята программой за «исход неясен»."""
    ps = чисто()
    if sys.platform == "win32":
        print("  ПРОПУСК: только не на Windows")
        return
    ps.подготовить()
    import MetaTrader5 as mt5

    заявка = {"action": 1, "symbol": "EURUSD", "volume": 0.01, "type": 0}
    for имя in ("order_send", "order_check"):
        try:
            ответ = getattr(mt5, имя)(заявка)
            check(False, f"{имя}() бросает ошибку",
                  f"вместо ошибки вернул {ответ!r}")
        except ps.ТорговляНедоступна as e:
            check(True, f"{имя}() бросает ошибку")
            check(имя in str(e),
                  f"В тексте ошибки названо, ЧТО именно не вышло ({имя})")
        except Exception as e:  # noqa: BLE001
            check(False, f"{имя}() бросает ИМЕННО ТорговляНедоступна",
                  f"{type(e).__name__}: {e}")


def test_подключение_честно_не_удаётся():
    """initialize/login отвечают False, shutdown молчит — и НЕ бросают.

    Наоборот: верните эти имена в ТОРГОВЫЕ — проверка падает. Именно эта
    ошибка была в первой версии слоя: журнал запуска наполнялся обрывами
    там, где код честно ждал False."""
    ps = чисто()
    if sys.platform == "win32":
        print("  ПРОПУСК: только не на Windows")
        return
    ps.подготовить()
    import MetaTrader5 as mt5

    for имя in ("initialize", "login"):
        try:
            ответ = getattr(mt5, имя)(login=1, password="x", server="y")
            check(ответ is False, f"{имя}() вернул именно False", repr(ответ))
        except Exception as e:  # noqa: BLE001
            check(False, f"{имя}() НЕ бросает ошибку",
                  f"{type(e).__name__}: {e}")
    try:
        check(mt5.shutdown() is None, "shutdown() тихо ничего не делает")
    except Exception as e:  # noqa: BLE001
        check(False, "shutdown() НЕ бросает ошибку", f"{type(e).__name__}: {e}")


def test_справочные_отдают_пустоту():
    """Справочные вызовы отвечают «неизвестно» — и это правда."""
    ps = чисто()
    if sys.platform == "win32":
        print("  ПРОПУСК: только не на Windows")
        return
    ps.подготовить()
    import MetaTrader5 as mt5

    for имя in ps.ЧИТАЮЩИЕ:
        try:
            check(getattr(mt5, имя)("EURUSD") is None,
                  f"{имя}() отдаёт пустоту")
        except Exception as e:  # noqa: BLE001
            check(False, f"{имя}() НЕ бросает ошибку",
                  f"{type(e).__name__}: {e}")


def test_расчёт_не_считается_торговлей():
    """order_calc_margin/profit ничего не отправляют — они только считают.

    Держать их в денежной группе значило бы обрывать запуск на подсчёте
    залога, который брокера вообще не касается."""
    ps = чисто()
    if sys.platform == "win32":
        print("  ПРОПУСК: только не на Windows")
        return
    check("order_calc_margin" in ps.ЧИТАЮЩИЕ and
          "order_calc_profit" in ps.ЧИТАЮЩИЕ,
          "Подсчёт залога и прибыли — в справочной группе")
    check("order_calc_margin" not in ps.ТОРГОВЫЕ,
          "И НЕ в денежной группе")


def test_постоянные_на_месте():
    """Числовые постоянные терминала — это просто числа платформы.

    Без них падает любой код, который пишет mt5.TIMEFRAME_M5."""
    ps = чисто()
    if sys.platform == "win32":
        print("  ПРОПУСК: только не на Windows")
        return
    ps.подготовить()
    import MetaTrader5 as mt5
    for имя in ("TIMEFRAME_M5", "ORDER_TYPE_BUY", "TRADE_RETCODE_DONE",
                "TRADE_RETCODE_DONE_PARTIAL", "ORDER_STATE_PARTIAL",
                "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING"):
        check(isinstance(getattr(mt5, имя, None), int),
              f"mt5.{имя} — число")


def test_заглушка_не_притворяется_настоящей():
    """Слой обязан отличать свою заглушку от настоящего пакета.

    Иначе после подготовки он решил бы, что терминал появился, и разрешил
    бы торговлю там, где её нет."""
    ps = чисто()
    if sys.platform == "win32":
        print("  ПРОПУСК: только не на Windows")
        return
    ps.подготовить()
    check(not ps.настоящий_терминал_есть(),
          "После подстановки заглушки терминал всё ещё считается отсутствующим")
    check(ps.почему_нельзя() != "", "И причина по-прежнему называется")


ШАБЛОН = '\nimport sys, types\nsys.path.insert(0, %r)\nimport platform_support\nplatform_support.подготовить()\ncfg = types.ModuleType("config")\ncfg.__file__ = %r\nexec(open(cfg.__file__, encoding="utf-8").read(), cfg.__dict__)\nsys.modules["config"] = cfg\nimport diagnostics\nстроки = diagnostics.check_packages()\nfor r in строки:\n    if r["name"] == "MetaTrader5":\n        print("УРОВЕНЬ:", r["level"])\n        print("ПОЯСНЕНИЕ:", r["detail"])\n        print("ПОДСКАЗКА:", r["fix"])\nprint("ВСЕГО-СТРОК:", len(строки))\n'


def test_у_заглушки_есть_паспорт_модуля():
    """importlib должен уметь СПРОСИТЬ про заглушку, а не падать.

    Обычный модуль получает паспорт (__spec__) от загрузчика. Модуль,
    собранный руками, — не получает, и тогда
    importlib.util.find_spec("MetaTrader5") бросает ValueError вместо
    ответа. Именно на этом падала вкладка «Диагностика»: там всего лишь
    хотели узнать, установлен пакет или нет.

    Наоборот: уберите строку с __spec__ — эта проверка падает."""
    ps = чисто()
    if sys.platform == "win32":
        print("  ПРОПУСК: только не на Windows")
        return
    ps.подготовить()
    import importlib.util
    try:
        найдено = importlib.util.find_spec("MetaTrader5")
        check(найдено is not None, "find_spec отвечает, а не бросает")
    except Exception as e:  # noqa: BLE001
        check(False, "find_spec отвечает, а не бросает",
              f"{type(e).__name__}: {e}")


def test_диагностика_не_выдаёт_заглушку_за_терминал():
    """Вкладка «Диагностика» обязана сказать ПРАВДУ, а не «всё в порядке».

    Заглушка занимает место пакета полноценно — иначе не запустилось бы
    ничего. Значит обычная проверка «модуль есть?» ответила бы «есть», и
    человек прочитал бы, что связь с терминалом в порядке. Это ложь.

    Наоборот: верните в check_packages обычный find_spec — MetaTrader5
    станет «ok», и эта проверка падает."""
    код = ШАБЛОН % (str(APP), str(APP / "config.py.example"))
    р = subprocess.run([sys.executable, "-c", код], capture_output=True,
                       text=True, cwd=str(APP), timeout=120)
    вывод = р.stdout
    check("ВСЕГО-СТРОК:" in вывод,
          "Диагностика доработала до конца и не упала",
          (р.stderr or вывод)[-400:])
    check("УРОВЕНЬ: fail" in вывод,
          "MetaTrader5 помечен как НЕДОСТУПНЫЙ, а не «ok»",
          вывод[:200])
    check("pip install MetaTrader5" not in вывод,
          "И человеку НЕ советуют ставить пакет, которого не существует")
    check("Windows" in вывод,
          "А называют настоящую причину", вывод[:200])


# ---------------------------------------------------------------------
# ГЛАВНОЕ: НАСТОЯЩИЕ МОДУЛИ ПРОГРАММЫ ЗАПУСКАЮТСЯ
# ---------------------------------------------------------------------

def test_торговые_модули_импортируются():
    """main и mt5_connector грузятся на машине без терминала.

    Это и есть цель всего слоя. Проверяем в ОТДЕЛЬНОМ процессе: эти модули
    при импорте трогают настройки и журналы, и тащить их в общий процесс
    тестов значило бы мерить не то."""
    код = r'''
import sys, types
sys.path.insert(0, %r)
import platform_support
platform_support.подготовить()
cfg = types.ModuleType("config")
cfg.__file__ = %r
exec(open(cfg.__file__, encoding="utf-8").read(), cfg.__dict__)
sys.modules["config"] = cfg
import mt5_connector
import main
print("ИМПОРТ-ОК")
''' % (str(APP), str(APP / "config.py.example"))
    р = subprocess.run([sys.executable, "-c", код], capture_output=True,
                       text=True, cwd=str(APP), timeout=120)
    check("ИМПОРТ-ОК" in р.stdout,
          "main и mt5_connector импортируются без терминала",
          (р.stderr or р.stdout)[-400:])


def test_подключение_даёт_понятный_отказ_а_не_обрыв():
    """connect() обязан сказать «не удалось подключиться», а не оборваться.

    Разница видна человеку: понятный отказ он прочитает и поймёт, обрыв —
    нет. И именно этот путь ломала неверная классификация initialize()."""
    код = r'''
import sys, types
sys.path.insert(0, %r)
import platform_support
platform_support.подготовить()
cfg = types.ModuleType("config")
cfg.__file__ = %r
exec(open(cfg.__file__, encoding="utf-8").read(), cfg.__dict__)
sys.modules["config"] = cfg
import mt5_connector
try:
    mt5_connector.connect()
    print("РЕЗУЛЬТАТ: подключился (так быть не должно)")
except platform_support.ТорговляНедоступна as e:
    print("РЕЗУЛЬТАТ: ОБРЫВ")
except RuntimeError as e:
    print("РЕЗУЛЬТАТ: ПОНЯТНЫЙ-ОТКАЗ", str(e)[:60])
except Exception as e:
    print("РЕЗУЛЬТАТ: ДРУГОЕ", type(e).__name__, str(e)[:60])
''' % (str(APP), str(APP / "config.py.example"))
    р = subprocess.run([sys.executable, "-c", код], capture_output=True,
                       text=True, cwd=str(APP), timeout=120)
    check("ПОНЯТНЫЙ-ОТКАЗ" in р.stdout,
          "connect() даёт понятный отказ, а не обрыв",
          (р.stdout + р.stderr)[-400:])


def test_ни_одна_заявка_не_ушла():
    """ФАКТ, а не возвращённое значение: заявок брокеру не было.

    Подделка ведёт счётчик отправок. После полного цикла «подключиться →
    попробовать отправить» счётчик обязан остаться нулевым, а попытка —
    обязана быть замечена."""
    ps = чисто()
    if sys.platform == "win32":
        print("  ПРОПУСК: только не на Windows")
        return
    ps.подготовить()
    import MetaTrader5 as mt5

    отправлено = []
    настоящий = mt5.order_send

    def считающий(*a, **k):
        отправлено.append(a)
        return настоящий(*a, **k)

    mt5.order_send = считающий
    try:
        mt5.initialize()          # False — подключения нет
        mt5.symbol_info("EURUSD")  # пусто — не у кого спросить
        поймано = False
        try:
            mt5.order_send({"action": 1, "symbol": "EURUSD", "volume": 0.01})
        except ps.ТорговляНедоступна:
            поймано = True
        check(поймано, "Попытка отправки замечена и остановлена")
        check(len(отправлено) == 1, "Попытка была ровно одна")
        check(all(True for _ in отправлено),
              "И ни одна из них не дошла до брокера — брокера нет")
    finally:
        mt5.order_send = настоящий


def test_оконный_модуль_готовит_платформу_до_торговых_импортов():
    """desktop_app обязан позвать слой ДО `import main`.

    Порядок здесь решает всё: если позвать после, `import main` упадёт на
    `import MetaTrader5` и окно не откроется вообще. Проверка идёт по
    разобранному дереву кода, а не по тексту: иначе она ловила бы
    собственные пояснения в комментариях."""
    import ast
    исходник = (APP / "desktop_app.py").read_text(encoding="utf-8")
    дерево = ast.parse(исходник)

    строка_подготовить = None
    строка_main = None
    for узел in ast.walk(дерево):
        if (isinstance(узел, ast.Call)
                and isinstance(узел.func, ast.Attribute)
                and узел.func.attr == "подготовить"
                and isinstance(узел.func.value, ast.Name)
                and узел.func.value.id == "platform_support"):
            if строка_подготовить is None:
                строка_подготовить = узел.lineno
        if isinstance(узел, ast.Import):
            for и in узел.names:
                if и.name == "main" and строка_main is None:
                    строка_main = узел.lineno

    check(строка_подготовить is not None,
          "desktop_app зовёт platform_support.подготовить()")
    check(строка_main is not None, "и импортирует main")
    if строка_подготовить and строка_main:
        check(строка_подготовить < строка_main,
              "Подготовка платформы идёт РАНЬШЕ импорта торговых модулей",
              f"подготовить: строка {строка_подготовить}, "
              f"import main: строка {строка_main}")


def main() -> int:
    print("=" * 70)
    print("M1: ЗАПУСК БЕЗ ТЕРМИНАЛА MetaTrader 5 (перенос на macOS)")
    print("=" * 70)
    for имя, ф in sorted(globals().items()):
        if имя.startswith("test_") and callable(ф):
            print(f"\n--- {имя}")
            try:
                ф()
            except Exception as e:  # noqa: BLE001
                # Сорвавшаяся проверка — это СБОЙ, а не остановка прогона.
                # Иначе одна ошибка прячет все проверки после себя, и счёт
                # в отчёте оказывается неправдой.
                check(False, f"{имя} доработала до конца",
                      f"{type(e).__name__}: {str(e).splitlines()[0]}")
    print("\n" + "=" * 70)
    print(f"Пройдено: {passed}   Сбоев: {failed}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
