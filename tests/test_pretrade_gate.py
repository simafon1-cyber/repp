#!/usr/bin/env python3
"""ДП-P0-1: ПРЕДТОРГОВЫЙ БАРЬЕР — НА КАКОМ СЧЁТЕ МОЖНО ТОРГОВАТЬ.

ПОЧЕМУ ЭТИ ТЕСТЫ ПОЯВИЛИСЬ

Аудит демо-приёмки нашёл блокирующую дыру (P0-1) и назвал её точно:
протокол требует работать только на демо-счёте MetaQuotes-Demo, но
требование это жило в ДОКУМЕНТЕ, а не в коде. Единственным барьером перед
отправкой заявки был флаг LIVE_TRADING — а он не отвечает на вопрос, НА
КАКОМ СЧЁТЕ программа сейчас работает.

Если MT5_LOGIN не заполнен, программа входит в первый готовый счёт из
хранилища терминала. Первым может оказаться не тот. В том числе реальный.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ ПО ФАКТУ, А НЕ ПО ВОЗВРАЩЁННОМУ ЗНАЧЕНИЮ

Главное доказательство в этом файле — СЧЁТЧИК обращений к order_send у
поддельного терминала. Он обязан остаться нулевым во всех запрещённых
случаях. Проверять «функция вернула False» тут мало: важно, что заявка
физически никуда не пошла.

ПОЧЕМУ БАРЬЕР ИМЕННО FAIL-CLOSED

Начальное состояние — «разрешения нет». Чтобы торговать, надо доказать
право; чтобы не торговать, достаточно ничего не делать. Обратный порядок
(«разрешено, пока не запретили») ломается от любой забытой ветки кода.

ЧЕГО ЭТИ ТЕСТЫ НЕ ПРОВЕРЯЮТ

Поведение у настоящего брокера. Здесь подделка, и она отвечает так, как
её научили. Зелёный результат тут не доказывает, что демо-счёт ответит
так же.

Запуск:  python3 tests/test_pretrade_gate.py
"""

from __future__ import annotations

import ast
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


# =====================================================================
# ПОДДЕЛЬНЫЙ ТЕРМИНАЛ, КОТОРЫЙ СЧИТАЕТ ОБРАЩЕНИЯ
# =====================================================================
class ПоддельныйТерминал(types.ModuleType):
    """Модуль MetaTrader5, который ведёт счёт обращений к order_send.

    Ведёт именно СЧЁТ, а не флаг: «ни одного» и «одно» — разные вещи, и
    отличать их надо числом."""

    def __init__(self, счёт=None, бросить=None):
        super().__init__("MetaTrader5")
        self.отправок = 0
        self.последняя_заявка = None
        self._счёт = счёт
        self._бросить = бросить

        # Постоянные терминала — просто числа платформы.
        self.ORDER_TYPE_BUY = 0
        self.ORDER_TYPE_SELL = 1
        self.TRADE_ACTION_DEAL = 1
        self.TRADE_ACTION_SLTP = 2
        self.ORDER_FILLING_IOC = 2
        self.ORDER_FILLING_FOK = 1
        self.ORDER_TIME_GTC = 0
        self.TRADE_RETCODE_DONE = 10009
        self.TRADE_RETCODE_REQUOTE = 10004
        self.TRADE_RETCODE_PRICE_CHANGED = 10020
        self.TRADE_RETCODE_PRICE_OFF = 10021
        self.TRADE_RETCODE_TIMEOUT = 10012
        self.TRADE_RETCODE_INVALID_FILL = 10030
        self.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING = 2
        self.ACCOUNT_MARGIN_MODE_RETAIL_NETTING = 0
        self.TIMEFRAME_M1 = 1
        self.TIMEFRAME_M5 = 5
        self.TIMEFRAME_M15 = 15
        self.TIMEFRAME_M30 = 30
        self.TIMEFRAME_H1 = 16385
        self.TIMEFRAME_H4 = 16388
        self.TIMEFRAME_D1 = 16408
        self.POSITION_TYPE_BUY = 0
        self.POSITION_TYPE_SELL = 1
        self.DEAL_TYPE_BUY = 0
        self.DEAL_TYPE_SELL = 1
        self.DEAL_ENTRY_IN = 0
        self.DEAL_ENTRY_OUT = 1
        self.TRADE_RETCODE_DONE_PARTIAL = 10010
        self.TRADE_RETCODE_PLACED = 10008
        self.TRADE_RETCODE_CONNECTION = 10031
        self.TRADE_RETCODE_REJECT = 10006
        self.TRADE_RETCODE_NO_MONEY = 10019
        self.TRADE_RETCODE_HEDGE_PROHIBITED = 10046
        self.SYMBOL_TRADE_MODE_FULL = 4
        self.ORDER_STATE_STARTED = 0
        self.ORDER_STATE_PLACED = 1
        self.ORDER_STATE_CANCELED = 2
        self.ORDER_STATE_PARTIAL = 3
        self.ORDER_STATE_FILLED = 4
        self.ORDER_STATE_REJECTED = 5
        self.ORDER_STATE_EXPIRED = 6
        self.ACCOUNT_MARGIN_MODE_EXCHANGE = 1

    # --- то, что двигает деньги ---
    def order_send(self, request):
        self.отправок += 1
        self.последняя_заявка = request
        return types.SimpleNamespace(retcode=self.TRADE_RETCODE_DONE,
                                     order=1, deal=1, volume=request.get("volume"))

    # --- то, что только спрашивают ---
    def account_info(self):
        if self._бросить:
            raise self._бросить
        return self._счёт

    def symbol_info_tick(self, symbol):
        return types.SimpleNamespace(bid=1.1, ask=1.1001)

    def symbol_info(self, symbol):
        return types.SimpleNamespace(filling_mode=2, point=0.00001,
                                     spread=10, digits=5, volume_step=0.01,
                                     trade_mode=4)

    def positions_get(self, **k):
        return []

    def last_error(self):
        return (0, "ok")

    def terminal_info(self):
        return types.SimpleNamespace(trade_allowed=True, trade_expert=True)

    def initialize(self, **k):
        return True

    def shutdown(self):
        return None


def настройки(**правки):
    """config для теста. По умолчанию — режим приёмки со ЗАДАННЫМ счётом."""
    cfg = types.ModuleType("config")
    cfg.__file__ = str(APP / "config.py.example")
    exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
    cfg.LIVE_TRADING = True
    cfg.DEMO_ACCEPTANCE_MODE = True
    cfg.DEMO_ACCEPTANCE_LOGIN = 5054028014
    cfg.DEMO_ACCEPTANCE_SERVER = "MetaQuotes-Demo"
    cfg.DEMO_ACCEPTANCE_REQUIRE_DEMO = True
    for имя, з in правки.items():
        setattr(cfg, имя, з)
    sys.modules["config"] = cfg
    return cfg


def счёт(login=5054028014, server="MetaQuotes-Demo", trade_mode=0):
    return types.SimpleNamespace(login=login, server=server,
                                 trade_mode=trade_mode, balance=10000.0,
                                 equity=10000.0, currency="USD",
                                 margin_free=10000.0)


def собрать(cfg, терминал):
    """Подставить подделки и загрузить связку заново."""
    for имя in ("MetaTrader5", "mt5_connector", "pretrade_gate"):
        sys.modules.pop(имя, None)
    sys.modules["MetaTrader5"] = терминал
    import pretrade_gate
    import mt5_connector
    pretrade_gate.закрыть()
    return pretrade_gate, mt5_connector


# =====================================================================
# 1. САМА ПРОВЕРКА: ЧТО ПРОПУСКАЕТ, ЧТО НЕТ
# =====================================================================

def test_по_умолчанию_барьер_закрыт():
    """Ничего не делали — торговать нельзя. Это и есть fail-closed.

    Наоборот: сделайте начальным состоянием «разрешено» — проверка
    падает, и вместе с ней исчезает вся защита."""
    cfg = настройки()
    g, _ = собрать(cfg, ПоддельныйТерминал(счёт=счёт()))
    check(not g.открыт(), "Свежий барьер закрыт")
    try:
        g.требовать("проверка")
        check(False, "Закрытый барьер не пропускает", "пропустил")
    except g.БарьерЗакрыт as e:
        check(True, "Закрытый барьер не пропускает")
        check("не доказано" in str(e),
              "И сказано, ПОЧЕМУ: право не доказано", str(e)[:70])


def test_верный_демо_счёт_пропускается():
    """Всё совпало — разрешение выдано."""
    cfg = настройки()
    g, _ = собрать(cfg, ПоддельныйТерминал(счёт=счёт()))
    можно, почему = g.открыть(cfg, lambda: счёт())
    check(можно, "Верный демо-счёт пропущен", почему)
    check(g.открыт(), "Барьер открыт")
    check(g.разрешение()["номер"] == 5054028014,
          "И записано, чем именно открыт")


def test_чужой_номер_счёта_не_пропускается():
    """Номер не тот — запрет. Именно этого и боялся аудит."""
    cfg = настройки()
    g, _ = собрать(cfg, ПоддельныйТерминал(счёт=счёт(login=777000111)))
    можно, почему = g.открыть(cfg, lambda: счёт(login=777000111))
    check(not можно, "Чужой счёт не пропущен")
    check("НЕ ТОТ" in почему, "И названа причина словами", почему[:80])
    check("777000111" in почему and "5054028014" in почему,
          "С обоими номерами: к какому подключились и какой разрешён")
    check(not g.открыт(), "Барьер остался закрытым")


def test_чужой_сервер_не_пропускается():
    """Номер совпал, а сервер нет — тоже запрет.

    У разных брокеров номера счетов повторяются: совпадение одного лишь
    номера не значит ничего."""
    cfg = настройки()
    g, _ = собрать(cfg, ПоддельныйТерминал(счёт=счёт(server="OtherBroker-Real")))
    можно, почему = g.открыть(cfg, lambda: счёт(server="OtherBroker-Real"))
    check(not можно, "Чужой сервер не пропущен")
    check("сервер НЕ ТОТ" in почему, "И названа причина", почему[:80])


def test_реальный_счёт_не_пропускается():
    """trade_mode не демо — запрет. Приёмка на реальные деньги не идёт."""
    cfg = настройки()
    g, _ = собрать(cfg, ПоддельныйТерминал(счёт=счёт(trade_mode=2)))
    можно, почему = g.открыть(cfg, lambda: счёт(trade_mode=2))
    check(not можно, "Реальный счёт не пропущен")
    check("РЕАЛЬНЫЙ" in почему, "И сказано прямо, что счёт реальный", почему[:80])


def test_молчание_терминала_это_запрет():
    """account_info() вернул пустоту — мы НЕ ЗНАЕМ, где мы. Значит нельзя.

    Раздел 5 правил проекта, перенесённый на вход: неясность — запрет, а
    не «наверное всё в порядке»."""
    cfg = настройки()
    g, _ = собрать(cfg, ПоддельныйТерминал(счёт=None))
    можно, почему = g.открыть(cfg, lambda: None)
    check(not можно, "Молчание терминала — запрет")
    check("НЕ ЗНАЕМ" in почему, "И сказано, что мы не знаем, а не что всё плохо",
          почему[:80])


def test_ошибка_при_вопросе_это_тоже_запрет():
    """Вопрос сорвался — тоже запрет, и причина названа."""
    cfg = настройки()
    g, _ = собрать(cfg, ПоддельныйТерминал())

    def ломается():
        raise RuntimeError("терминал отвалился")

    можно, почему = g.открыть(cfg, ломается)
    check(not можно, "Сорвавшийся вопрос — запрет")
    check("RuntimeError" in почему, "И названа причина", почему[:80])
    check(not g.открыт(), "Барьер закрыт")


def test_незаданный_счёт_это_запрет():
    """Режим включён, а ожидаемый счёт не задан — запрет.

    Иначе барьер сравнивал бы счёт неизвестно с чем и пропускал всё
    подряд, создавая ложное чувство защищённости."""
    cfg = настройки(DEMO_ACCEPTANCE_LOGIN=0)
    g, _ = собрать(cfg, ПоддельныйТерминал(счёт=счёт()))
    можно, почему = g.открыть(cfg, lambda: счёт())
    check(not можно, "Незаданный ожидаемый счёт — запрет")
    check("не задан" in почему, "И сказано, чего именно не хватает", почему[:80])


def test_любой_демо_счёт_пропускается():
    """Владелец: «счета будут всегда разные, он должен сам адаптироваться».

    Номер и сервер не сверяются — но только среди ДЕМО-счетов."""
    print("\n[Режим «любой демо-счёт»]")
    cfg = настройки(DEMO_ACCEPTANCE_ANY_DEMO=True,
                    DEMO_ACCEPTANCE_LOGIN=0, DEMO_ACCEPTANCE_SERVER="")
    g, _ = собрать(cfg, ПоддельныйТерминал(счёт=счёт()))
    можно, почему = g.проверить(cfg, счёт(login=5055292295,
                                          server="MetaQuotes-Demo"))
    check(можно, "Новый демо-счёт пропущен, хотя номер не записан", почему)
    check("5055292295" in почему, "И назван номер, на который открыт", почему)

    можно, почему = g.проверить(cfg, счёт(login=777, server="Другой-Demo"))
    check(можно, "И на другом сервере тоже — сервер не сверяется", почему)


def test_любой_демо_не_пускает_на_реальный_счёт():
    """Главная проверка послабления: деньги остаются защищены."""
    print("\n[«Любой демо» не значит «любой счёт»]")
    cfg = настройки(DEMO_ACCEPTANCE_ANY_DEMO=True,
                    DEMO_ACCEPTANCE_LOGIN=0, DEMO_ACCEPTANCE_SERVER="")
    g, _ = собрать(cfg, ПоддельныйТерминал(счёт=счёт()))
    можно, почему = g.проверить(cfg, счёт(login=5055292295,
                                          trade_mode=2))   # реальный
    check(not можно, "РЕАЛЬНЫЙ счёт НЕ пропущен", почему)
    check("ДЕМОНСТРАЦИОННЫЕ" in почему, "И сказано почему", почему)


def test_любой_демо_без_требования_демо_запрещён():
    """Два послабления вместе открыли бы дорогу к реальным деньгам."""
    print("\n[«Любой демо» + снято требование демо]")
    cfg = настройки(DEMO_ACCEPTANCE_ANY_DEMO=True,
                    DEMO_ACCEPTANCE_REQUIRE_DEMO=False,
                    DEMO_ACCEPTANCE_LOGIN=0, DEMO_ACCEPTANCE_SERVER="")
    g, _ = собрать(cfg, ПоддельныйТерминал(счёт=счёт()))
    можно, почему = g.проверить(cfg, счёт(trade_mode=0))
    check(not можно, "Сочетание запрещено даже на демо-счёте", почему)
    check("РЕАЛЬНОМ" in почему, "И названа причина — так можно уйти в реал",
          почему)


def test_любой_демо_молчание_терминала_это_запрет():
    print("\n[«Любой демо»: терминал молчит]")
    cfg = настройки(DEMO_ACCEPTANCE_ANY_DEMO=True,
                    DEMO_ACCEPTANCE_LOGIN=0, DEMO_ACCEPTANCE_SERVER="")
    g, _ = собрать(cfg, ПоддельныйТерминал(счёт=счёт()))
    можно, почему = g.проверить(cfg, None)
    check(not можно, "Нет ответа — нет разрешения", почему)
    можно, почему = g.проверить(cfg, счёт(trade_mode=None))
    check(not можно, "Режим счёта неизвестен — тоже запрет", почему)


def test_выключенный_режим_ничего_не_запрещает():
    """Режим приёмки выключен — барьер не вмешивается.

    Это важно проверить отдельно: барьер добавлен ПЕРЕД существующими
    проверками и не имеет права менять поведение обычной работы."""
    cfg = настройки(DEMO_ACCEPTANCE_MODE=False, DEMO_ACCEPTANCE_LOGIN=0)
    g, _ = собрать(cfg, ПоддельныйТерминал(счёт=счёт(login=1, trade_mode=2)))
    можно, почему = g.открыть(cfg, lambda: счёт(login=1, trade_mode=2))
    check(можно, "С выключенным режимом барьер пропускает", почему)


# =====================================================================
# 2. ПО ФАКТУ: ЗАЯВКА ФИЗИЧЕСКИ НЕ УХОДИТ
# =====================================================================

def test_при_закрытом_барьере_ни_одной_отправки():
    """ГЛАВНАЯ проверка файла. Считаем обращения к order_send.

    Проверяется не возвращённое значение, а ФАКТ: счётчик поддельного
    терминала обязан остаться нулевым.

    Наоборот: уберите вызов требовать() из send_market_order — счётчик
    станет единицей, и проверка падает."""
    cfg = настройки()
    терминал = ПоддельныйТерминал(счёт=счёт(login=777000111))
    g, mt5c = собрать(cfg, терминал)

    можно, _ = g.открыть(cfg, lambda: счёт(login=777000111))
    check(not можно, "Барьер закрыт: счёт чужой")

    поймано = False
    try:
        mt5c.send_market_order("EURUSD", 1, 0.01, 1.0, 1.2, 234567)
    except g.БарьерЗакрыт:
        поймано = True
    check(поймано, "Попытка открыть позицию остановлена")
    check(терминал.отправок == 0,
          "И ни одной отправки брокеру НЕ БЫЛО", str(терминал.отправок))


def test_закрытие_и_изменение_тоже_остановлены():
    """Закрыть позицию и подвинуть стоп — такие же торговые вызовы.

    Барьер, который стережёт только открытие, не барьер: закрытие на
    чужом счёте — тоже действие с чужими деньгами."""
    cfg = настройки()
    терминал = ПоддельныйТерминал(счёт=счёт(trade_mode=2))
    g, mt5c = собрать(cfg, терминал)
    g.открыть(cfg, lambda: счёт(trade_mode=2))

    остановлено = 0
    try:
        mt5c.modify_position(111, 1.0, 1.2)
    except g.БарьерЗакрыт:
        остановлено += 1
    позиция = types.SimpleNamespace(ticket=111, symbol="EURUSD", type=0,
                                    magic=234567, volume=0.02)
    try:
        mt5c.close_position_partial(позиция, 0.01)
    except g.БарьерЗакрыт:
        остановлено += 1

    check(остановлено == 2, "Оба вызова остановлены", str(остановлено))
    check(терминал.отправок == 0,
          "И отправок по-прежнему ноль", str(терминал.отправок))


def test_с_открытым_барьером_заявка_проходит():
    """Барьер не должен ломать нормальную работу.

    Проверка нужна ровно затем, чтобы «безопасность» не оказалась
    поломкой: на верном демо-счёте заявка обязана уйти как раньше."""
    cfg = настройки()
    терминал = ПоддельныйТерминал(счёт=счёт())
    g, mt5c = собрать(cfg, терминал)
    можно, _ = g.открыть(cfg, lambda: счёт())
    check(можно, "Барьер открыт на верном демо-счёте")

    ответ = mt5c.send_market_order("EURUSD", 1, 0.01, 1.0, 1.2, 234567)
    check(терминал.отправок == 1, "Заявка ушла ровно одна",
          str(терминал.отправок))
    check(ответ is not None and ответ.retcode == 10009,
          "И ответ брокера получен как обычно")


def test_отзыв_разрешения_немедленно_запрещает():
    """Барьер можно закрыть обратно, и это действует сразу."""
    cfg = настройки()
    терминал = ПоддельныйТерминал(счёт=счёт())
    g, mt5c = собрать(cfg, терминал)
    g.открыть(cfg, lambda: счёт())
    mt5c.send_market_order("EURUSD", 1, 0.01, 1.0, 1.2, 234567)
    было = терминал.отправок

    g.закрыть()
    поймано = False
    try:
        mt5c.send_market_order("EURUSD", 1, 0.01, 1.0, 1.2, 234567)
    except g.БарьерЗакрыт:
        поймано = True
    check(поймано, "После отзыва разрешения заявка остановлена")
    check(терминал.отправок == было,
          "И счётчик отправок не вырос", f"{было} -> {терминал.отправок}")


def test_с_выключенным_режимом_торговля_идёт_как_раньше():
    """Барьер не имеет права ломать тех, кто приёмку не проводит.

    Раздел 4 правил проекта: торговую логику без согласования не трогать.
    Значит при выключенном режиме заявка обязана уйти БЕЗ всякого
    открытия барьера — ровно как до его появления.

    Наоборот: пусть требовать() запрещает всегда, не глядя на настройки, —
    проверка падает, и вместе с ней ломается обычная работа программы."""
    cfg = настройки(DEMO_ACCEPTANCE_MODE=False)
    терминал = ПоддельныйТерминал(счёт=счёт())
    g, mt5c = собрать(cfg, терминал)
    check(not g.открыт(), "Барьер не открывали")

    mt5c.send_market_order("EURUSD", 1, 0.01, 1.0, 1.2, 234567)
    check(терминал.отправок == 1,
          "И заявка всё равно ушла — обычная работа не сломана",
          str(терминал.отправок))


def test_нечитаемые_настройки_это_запрет():
    """Настройки не прочитались — запрет, а не «наверное режим выключен».

    Мы в этом случае не знаем даже, идёт ли приёмка. Делать из незнания
    вывод «можно» — та же ошибка, что и везде в этом проекте."""
    cfg = настройки()
    терминал = ПоддельныйТерминал(счёт=счёт())
    g, mt5c = собрать(cfg, терминал)

    было = sys.modules.pop("config", None)

    # Убираем config совсем: import внутри требовать() обязан сорваться.
    sys.path_importer_cache.clear()
    сохранён = list(sys.path)
    sys.path[:] = [п for п in sys.path if "ai_scalper_standalone" not in п]
    try:
        поймано = False
        try:
            mt5c.send_market_order("EURUSD", 1, 0.01, 1.0, 1.2, 234567)
        except g.БарьерЗакрыт as e:
            поймано = "прочитать не удалось" in str(e)
        check(поймано, "Без настроек торговля запрещена, и причина названа")
        check(терминал.отправок == 0, "И отправок ноль", str(терминал.отправок))
    finally:
        sys.path[:] = сохранён
        if было is not None:
            sys.modules["config"] = было


# =====================================================================
# 3. БАРЬЕР СТОИТ В РАБОЧЕМ ПУТИ, А НЕ РЯДОМ С НИМ
# =====================================================================

def test_барьер_стоит_во_всех_отправляющих_функциях():
    """Разбор дерева кода: у каждой функции, зовущей order_send, первой
    строкой обязан стоять вызов требовать().

    Проверка по дереву, а не по тексту: поиск по тексту нашёл бы слово
    «требовать» в этой самой шапке и в комментариях.

    Наоборот: уберите требовать() из любой из них — проверка падает и
    называет, из какой именно."""
    беззащитные = []
    for файл in ("mt5_connector.py", "account_supervisor.py"):
        дерево = ast.parse((APP / файл).read_text(encoding="utf-8"))
        for узел in ast.walk(дерево):
            if not isinstance(узел, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            шлёт = any(
                isinstance(в, ast.Call) and isinstance(в.func, ast.Attribute)
                and в.func.attr in ("order_send", "order_check")
                for в in ast.walk(узел))
            if not шлёт:
                continue
            есть_барьер = any(
                isinstance(в, ast.Call) and isinstance(в.func, ast.Attribute)
                and в.func.attr == "требовать"
                for в in ast.walk(узел))
            if not есть_барьер:
                беззащитные.append(f"{файл}:{узел.name}")
    check(not беззащитные,
          "У каждой отправляющей функции стоит предторговый барьер",
          "; ".join(беззащитные))


def test_настройки_барьера_нельзя_менять_удалённо():
    """Раздел 5 правил проекта.

    Тот, кто может выключить барьер по сети, может увести заявки на чужой
    счёт. Значит эти настройки удалённо не меняются ни в какую сторону."""
    настройки()
    sys.modules.pop("remote_settings", None)
    import remote_settings
    принято, отброшено = remote_settings.validate({
        "DEMO_ACCEPTANCE_MODE": False,
        "DEMO_ACCEPTANCE_LOGIN": 777,
        "DEMO_ACCEPTANCE_SERVER": "Other-Real",
        "DEMO_ACCEPTANCE_REQUIRE_DEMO": False,
    })
    check(принято == {}, "Ни одна настройка барьера не принята", str(принято))
    check(len(отброшено) == 4, "Все четыре отброшены", str(len(отброшено)))
    for имя in ("DEMO_ACCEPTANCE_MODE", "DEMO_ACCEPTANCE_LOGIN",
                "DEMO_ACCEPTANCE_SERVER", "DEMO_ACCEPTANCE_REQUIRE_DEMO"):
        check(any(имя in с for с in отброшено), f"{имя} названа в отказе")


def main() -> int:
    print("=" * 70)
    print("ДП-P0-1: ПРЕДТОРГОВЫЙ БАРЬЕР")
    print("=" * 70)
    for имя, ф in sorted(globals().items()):
        if имя.startswith("test_") and callable(ф):
            print(f"\n--- {имя}")
            try:
                ф()
            except Exception as e:  # noqa: BLE001
                check(False, f"{имя} доработала до конца",
                      f"{type(e).__name__}: {str(e).splitlines()[0]}")
    print("\n" + "=" * 70)
    print(f"Пройдено: {passed}   Сбоев: {failed}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
