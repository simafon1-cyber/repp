#!/usr/bin/env python3
"""Тесты определения «рынок закрыт или неликвиден».

ОТКУДА ЗАДАЧА. Разбор отчёта владельца показал, что весь минус счёта дала
ночь: 22 сделки с 22:00 до 02:00 дали -20.66 при итоге счёта -19.99.
Напрашивался запрет по часам. Владелец возразил точнее: «не по моему
времени, а когда именно рынок закрыт».

Он прав, и часы тут действительно плохая мера: время компьютера и время
сервера брокера расходятся на 2-3 часа, у разных брокеров по-разному, а час
сам по себе не причина убытка — причина в состоянии рынка.

ЧТО ПРОВЕРЯЕТСЯ. Не «функция вернула строку», а свойства, ради которых всё
писалось: часовые пояса не участвуют в расчёте вообще; молчание при нехватке
данных вместо выдуманного запрета; профиль «Истеричка» не может отменить эту
проверку; открытые сделки не трогаются.

Запуск:  python3 tests/test_market_hours.py
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


def code_only(text: str) -> str:
    """Текст файла без комментариев и строк документации: проверка не должна
    срабатывать на слово, встреченное в объяснении."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            node.value.value = ""
    return ast.unparse(tree)


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  OK   {name}")
    else:
        failed += 1
        print(f"  СБОЙ {name}" + (f"  -> {detail}" if detail else ""))


cfg = types.ModuleType("config")
cfg.__file__ = str(APP / "config.py.example")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg
CFG = cfg


class _FakeMT5(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return name


sys.modules["MetaTrader5"] = _FakeMT5("MetaTrader5")

import config_migrate as cm      # noqa: E402
import market_hours as mh        # noqa: E402


# =====================================================================
# ЧАСОВЫЕ ПОЯСА НЕ УЧАСТВУЮТ
# =====================================================================
def test_timezone_never_enters_the_math() -> None:
    """Главное свойство. Отметка времени в MT5 — это время сервера брокера,
    а не UTC. Вычитать её из наших часов нельзя: разница в 2-3 часа
    превратила бы свежую котировку в «замерла на три часа»."""
    print("\n[Часовой пояс брокера не влияет на расчёт]")
    mh.reset()

    # Сервер брокера на 3 часа впереди — отметки времени огромные.
    ahead = 1_800_000_000 + 3 * 3600
    mh.note_quote("EURUSD", ahead, now=1000.0)
    check(mh.frozen_seconds("EURUSD", now=1000.0) == 0.0,
          "Первая котировка — цена не «замерла»")
    check(mh.frozen_seconds("EURUSD", now=1010.0) == 10.0,
          "Через 10 наших секунд — ровно 10, а не разница поясов",
          str(mh.frozen_seconds("EURUSD", now=1010.0)))

    # Сервер брокера на 3 часа ПОЗАДИ — результат обязан быть тем же.
    mh.reset()
    behind = 1_800_000_000 - 3 * 3600
    mh.note_quote("EURUSD", behind, now=1000.0)
    check(mh.frozen_seconds("EURUSD", now=1010.0) == 10.0,
          "Сервер позади — ответ тот же самый",
          str(mh.frozen_seconds("EURUSD", now=1010.0)))

    # ГЛАВНЫЙ СЛУЧАЙ, ради которого всё писалось: замер повторяется каждый
    # проход, а котировка НЕ МЕНЯЕТСЯ. Именно так выглядит замерший рынок, и
    # именно так модуль вызывается в жизни — а не по одному разу, как выше.
    mh.reset()
    frozen_tick = 1_800_000_000
    mh.note_quote("EURUSD", frozen_tick, now=1000.0)
    check(mh.note_quote("EURUSD", frozen_tick, now=1030.0) == 30.0,
          "Та же котировка через 30 с — цена стоит 30 с",
          str(mh.note_quote("EURUSD", frozen_tick, now=1030.0)))
    check(mh.note_quote("EURUSD", frozen_tick, now=1200.0) == 200.0,
          "Повторные замеры НЕ сбрасывают отсчёт",
          str(mh.note_quote("EURUSD", frozen_tick, now=1200.0)))
    check(mh.frozen_seconds("EURUSD", now=1200.0) == 200.0,
          "И снаружи видно то же самое")

    # А как только котировка сменилась — рынок ожил, отсчёт с нуля
    check(mh.note_quote("EURUSD", frozen_tick + 1, now=1200.0) == 0.0,
          "Новая котировка — отсчёт сброшен")
    check(mh.frozen_seconds("EURUSD", now=1205.0) == 5.0,
          "И пошёл заново")

    # Цена изменилась — отсчёт с нуля
    mh.reset()
    mh.note_quote("EURUSD", behind, now=1000.0)
    mh.note_quote("EURUSD", behind + 1, now=1010.0)
    check(mh.frozen_seconds("EURUSD", now=1010.0) == 0.0,
          "Цена обновилась — рынок жив, отсчёт сброшен")
    check(mh.frozen_seconds("EURUSD", now=1100.0) == 90.0,
          "И снова растёт по нашим часам")

    # Модуль не должен ЗНАТЬ про время суток вообще: ни календаря, ни часов,
    # ни зон. Всё, что ему нужно, — монотонный счётчик секунд.
    body = code_only((APP / "market_hours.py").read_text(encoding="utf-8"))
    forbidden = [w for w in ("datetime", "utcnow", "timezone", "localtime",
                             "strftime", ".hour", "tzinfo") if w in body]
    check(not forbidden,
          "В модуле нет ни календаря, ни часов, ни поясов",
          ", ".join(forbidden))


def test_frozen_reason() -> None:
    print("\n[Замершая цена]")
    check(mh.frozen_reason(10, 90) == "", "10 секунд — нормально")
    check(mh.frozen_reason(89.9, 90) == "", "Чуть ниже порога — молчим")
    check(mh.frozen_reason(90, 90) != "", "Ровно на пороге — уже причина")
    check("90" in mh.frozen_reason(90, 90),
          "В причине названо, сколько именно стояла цена",
          mh.frozen_reason(90, 90))
    check(mh.frozen_reason(99999, 0) == "",
          "Нулевой порог = проверка выключена")


def test_broker_says_closed() -> None:
    """Самый надёжный признак — прямой ответ брокера."""
    print("\n[Брокер закрыл торговлю по инструменту]")
    check(mh.trade_disabled_reason(0) != "",
          "trade_mode = 0 (DISABLED) — торговать нельзя")
    check("брокер" in mh.trade_disabled_reason(0),
          "И сказано, что это решение брокера", mh.trade_disabled_reason(0))
    check(mh.trade_disabled_reason(4) == "", "Обычный режим — можно")
    check(mh.trade_disabled_reason(None) == "",
          "Описания символа нет — это НЕ запрет, молчим")
    check(mh.trade_disabled_reason("мусор") == "",
          "Мусор вместо режима — тоже молчим, а не выдумываем запрет")


# =====================================================================
# НЕЛИКВИДНОСТЬ
# =====================================================================
def _fill_baseline(symbol: str, values, start: float = 0.0):
    """Набить долгую норму: замер раз в минуту, как в жизни."""
    for i, v in enumerate(values):
        mh.note_spread(symbol, v, now=start + i * mh.BASELINE_SECONDS)


def test_normal_is_calm_market_not_average_day() -> None:
    """Норма — это спред СПОКОЙНОГО рынка, а не средний по суткам.

    Медиана суток на паре, которая полночи стоит неликвидной, сама наполовину
    состоит из ночи. Тогда ночной спред перестаёт считаться широким — ровно
    тогда, когда он опаснее всего. Поэтому берётся нижний квартиль."""
    print("\n[Норма — спред спокойного рынка]")
    mh.reset()
    # Половина суток спокойная (10), половина неликвидная (60)
    _fill_baseline("EURUSD", [10] * 400 + [60] * 400)
    norm = mh.normal_spread("EURUSD")
    check(norm == 10, "Нижний квартиль показывает спокойный спред", str(norm))

    import statistics
    middle = statistics.median([10] * 400 + [60] * 400)
    check(norm < middle,
          "А медиана суток была бы выше и обезоружила бы проверку",
          f"квартиль {norm}, медиана {middle}")


def test_sustained_wide_spread_is_caught() -> None:
    """ТА САМАЯ ДЫРА, ради которой всё переделывалось.

    Раньше норма считалась по короткому окну в 17 минут. Ночью спред широкий
    ВСЁ ВРЕМЯ — окно за те же 17 минут целиком заполнялось ночными замерами,
    норма становилась ночной, отношение «текущий к обычному» равнялось
    единице, и порог 2.5 был недостижим в принципе. Защита ловила только
    резкие скачки и была слепа к затяжной неликвидности.

    Отчёт владельца за 12.08.2026 показал это в лоб: торговля шла с 00:01 до
    05:33 и дала -12.60, а вход не был закрыт ни разу."""
    print("\n[Затяжной широкий спред ловится, а не считается нормой]")
    mh.reset()

    # Сутки спокойной торговли: норма выучена
    _fill_baseline("EURUSD", [10] * 600)
    check(mh.normal_spread("EURUSD") == 10, "Норма выучена по спокойному рынку")

    # Наступила ночь: спред 40 и держится ЧАСАМИ, а не скачком
    night_start = 600 * mh.BASELINE_SECONDS
    _fill_baseline("EURUSD", [40] * 300, start=night_start)

    norm = mh.normal_spread("EURUSD")
    check(norm <= 12,
          "Норма НЕ уехала за ночным спредом — иначе ночь снова стала бы «нормой»",
          str(norm))
    reason = mh.thin_reason(40, norm, mh.spread_samples("EURUSD"), 2.5, 30)
    check(reason != "",
          "И затяжной ночной спред объявлен неликвидом", reason or "(пусто)")


def test_baseline_is_throttled_to_once_a_minute() -> None:
    """Норма должна пополняться РЕДКО — иначе она снова станет короткой.

    Торговый цикл зовёт замер каждые несколько секунд. Если складывать в
    норму каждый такой замер, то 1440 ячеек закончатся за пару часов, и
    «сутки наблюдений» превратятся в «последние два часа» — то есть ночью
    норма опять станет ночной. Это ровно та дыра, которую чинили."""
    print("\n[Норма пополняется не чаще раза в минуту]")
    mh.reset()
    # Двести замеров за одну минуту — так и ходит торговый цикл
    for i in range(200):
        mh.note_spread("EURUSD", 10, now=1000.0 + i * 0.3)
    check(mh.spread_samples("EURUSD") == 1,
          "За минуту в норму попал ровно один замер",
          str(mh.spread_samples("EURUSD")))
    check(mh.short_samples("EURUSD") == 200,
          "А короткое окно приняло все — оно про «сейчас»",
          str(mh.short_samples("EURUSD")))

    mh.note_spread("EURUSD", 10, now=1000.0 + mh.BASELINE_SECONDS)
    check(mh.spread_samples("EURUSD") == 2,
          "Через минуту добавился второй")

    # Сколько реального времени охватывает полная норма
    hours = mh.BASELINE_SAMPLES * mh.BASELINE_SECONDS / 3600
    check(hours >= 24, "Полная норма охватывает не меньше суток",
          f"{hours:.0f} ч")


def test_restart_does_not_disable_the_guard() -> None:
    """ЖАЛОБА ВЛАДЕЛЬЦА: «скачал обновление, перезакрыл, заново открыл — и
    опять пошли сделки».

    Причину создал я. Защита не судит, пока не накопит час наблюдений — это
    правильно, судить не с чем. Но после КАЖДОЙ установки программы
    наблюдений нет, и первый час защиты не было вовсе. Перезапуск буквально
    снимал её на час.

    Ждать не нужно: MetaTrader хранит спред в каждом баре, и суточная норма
    берётся из истории сразу."""
    print("\n[Перезапуск не снимает защиту на час]")
    mh.reset()
    check(mh.normal_spread("EURUSD") == 0.0,
          "Сразу после запуска нормы нет — судить не с чем")
    check(mh.thin_reason(100, mh.normal_spread("EURUSD"),
                         mh.spread_samples("EURUSD"), 2.5, 30) == "",
          "И широкий спред в этот момент не ловится — вот она, дыра")

    # История из баров: сутки спокойного рынка
    seeded = mh.seed_baseline("EURUSD", [10] * 1440)
    check(seeded > 0, "Норма взята из истории", str(seeded))
    check(mh.normal_spread("EURUSD") == 10, "И это спокойный спред")
    check(mh.thin_reason(40, mh.normal_spread("EURUSD"),
                         mh.spread_samples("EURUSD"), 2.5, 30) != "",
          "Теперь широкий спред ловится СРАЗУ, без часа ожидания")


def test_seeding_never_overwrites_live_data() -> None:
    """Живые замеры точнее исторических: история усреднена по минуте, а мы
    видим спред как он есть. Затирать своё чужим нельзя."""
    print("\n[История не затирает собственные наблюдения]")
    mh.reset()
    _fill_baseline("EURUSD", [30] * 200)          # свои наблюдения
    before = mh.normal_spread("EURUSD")
    check(mh.seed_baseline("EURUSD", [5] * 1440) == 0,
          "Своих наблюдений хватает — история не подставляется")
    check(mh.normal_spread("EURUSD") == before,
          "И норма не изменилась", f"{before} -> {mh.normal_spread('EURUSD')}")

    # А если своих мало — история нужна
    mh.reset()
    _fill_baseline("EURUSD", [30] * 5)
    check(mh.seed_baseline("EURUSD", [5] * 1440) > 0,
          "Своих мало — историю берём")

    # Мусор в истории не должен ломать норму
    mh.reset()
    check(mh.seed_baseline("EURUSD", []) == 0, "Пустая история — ничего")
    check(mh.seed_baseline("EURUSD", [0, -3, "мусор", None]) == 0,
          "Негодные значения отброшены целиком")
    check(mh.seed_baseline("EURUSD", [10, "мусор", 12]) > 0,
          "А годные из смеси — берутся")


def test_seeding_is_wired_at_startup() -> None:
    print("\n[Норма из истории берётся при запуске]")
    src = (APP / "main.py").read_text(encoding="utf-8")
    check("def seed_spread_baselines" in src, "Функция есть")
    check("seed_spread_baselines(" in src.split("while True:", 1)[1],
          "И вызывается из торгового цикла")
    body = src.split("def seed_spread_baselines", 1)[1].split("\ndef ", 1)[0]
    check('"M1"' in body, "Берутся МИНУТНЫЕ бары — по ним и считается норма")
    check("BASELINE_SAMPLES" in body,
          "Столько же баров, сколько ячеек в норме (сутки)")
    check('"spread"' in body, "Из баров берётся именно колонка спреда")
    # Один раз за запуск, а не каждую итерацию: 1440 баров на пару — дорого
    loop = src.split("while True:", 1)[1]
    check("_baselines_seeded" in loop,
          "Делается один раз за запуск, а не на каждом проходе")


def test_no_verdict_without_enough_history() -> None:
    print("\n[Без накопленной нормы не судим]")
    mh.reset()
    _fill_baseline("GBPUSD", [10] * (mh.BASELINE_MIN_SAMPLES - 1))
    check(mh.normal_spread("GBPUSD") == 0.0,
          "Меньше часа наблюдений — нормы нет")
    check(mh.market_block_reason("GBPUSD", trade_mode=4, spread_points=999,
                                 dead_seconds=0, thin_ratio=2.5,
                                 thin_min_samples=30) == "",
          "И вход не закрывается наугад")
    _fill_baseline("GBPUSD", [10] * 5,
                   start=mh.BASELINE_MIN_SAMPLES * mh.BASELINE_SECONDS)
    check(mh.normal_spread("GBPUSD") > 0, "Набралось — норма появилась")


def test_baseline_survives_restart() -> None:
    """Перезапуск НОЧЬЮ не должен заставить программу учить норму заново по
    ночным же замерам — а перезапускают её как раз тогда, когда что-то пошло
    не так."""
    print("\n[Норма переживает перезапуск]")
    import tempfile, os as _os
    mh.reset()
    _fill_baseline("EURUSD", [10] * 300)
    with tempfile.TemporaryDirectory() as d:
        path = _os.path.join(d, "baseline.json")
        check(mh.save_baseline(path) is True, "Норма сохранена в файл")
        mh.reset()
        check(mh.normal_spread("EURUSD") == 0.0, "После сброса нормы нет")
        check(mh.load_baseline(path) == 1, "Норма прочитана обратно")
        check(mh.normal_spread("EURUSD") == 10, "И это тот же спокойный спред")

        # Испорченный файл не должен ронять программу
        with open(path, "w", encoding="utf-8") as f:
            f.write("{это не json")
        mh.reset()
        check(mh.load_baseline(path) == 0, "Испорченный файл — просто нет нормы")
        check(mh.load_baseline(_os.path.join(d, "нет-такого")) == 0,
              "Отсутствующий файл — то же самое")


def test_thin_market() -> None:
    print("\n[Рынок неликвиден: спред шире обычного для этой же пары]")
    mh.reset()
    for _ in range(50):
        mh.note_spread("EURUSD", 10)

    check(mh.thin_reason(20, 10, 50, 2.5, 30) == "",
          "Вдвое шире при пороге 2.5 — ещё терпимо")
    check(mh.thin_reason(25, 10, 50, 2.5, 30) != "",
          "В 2.5 раза шире — рынок неликвиден")
    r = mh.thin_reason(30, 10, 50, 2.5, 30)
    check("30" in r and "10" in r,
          "В причине и текущий спред, и обычный", r)

    # Порог относительный: та же тройная ширина ловится на любом инструменте
    check(mh.thin_reason(300, 100, 50, 2.5, 30) != "",
          "Инструмент с широким нормальным спредом — правило то же")
    check(mh.thin_reason(150, 100, 50, 2.5, 30) == "",
          "И наоборот: 150 при норме 100 — это норма, а не неликвид")


def test_silence_when_we_do_not_know() -> None:
    """Молчать надёжнее, чем выдумать запрет: ложный запрет останавливает
    торговлю на ровном месте, а мы этого специально избегаем."""
    print("\n[Нехватка данных — молчим, а не запрещаем]")
    check(mh.thin_reason(100, 10, 5, 2.5, 30) == "",
          "Замеров всего 5 при минимуме 30 — не судим")
    check(mh.thin_reason(100, 0, 50, 2.5, 30) == "",
          "Обычный спред неизвестен (0) — не судим")
    check(mh.thin_reason(100, 10, 50, 0, 30) == "",
          "Нулевое отношение = проверка выключена")
    check(mh.thin_reason(0, 10, 50, 2.5, 30) == "",
          "Текущий спред нулевой (нет связи) — не судим")
    check(mh.thin_reason(None, 10, 50, 2.5, 30) == "",
          "None вместо спреда — не падаем и не судим")

    mh.reset()
    mh.note_spread("GBPUSD", 0)
    mh.note_spread("GBPUSD", -5)
    mh.note_spread("GBPUSD", "мусор")
    check(mh.spread_samples("GBPUSD") == 0,
          "Нулевые, отрицательные и нечисловые замеры не попадают в статистику")


def test_priority_of_reasons() -> None:
    """Человеку показывается самая НАДЁЖНАЯ из сработавших причин."""
    print("\n[Порядок причин: сначала брокер, потом цена, потом наша оценка]")
    mh.reset()
    # Норму набиваем через _fill_baseline: замеры должны быть РАЗНЕСЕНЫ ПО
    # ВРЕМЕНИ, иначе в долгую норму попадёт только один из них — она
    # пополняется не чаще раза в минуту.
    _fill_baseline("EURUSD", [10] * 200)
    mh.note_quote("EURUSD", 111, now=0.0)

    # Сработали все три сразу
    r = mh.market_block_reason("EURUSD", trade_mode=0, spread_points=100,
                               dead_seconds=90, thin_ratio=2.5,
                               thin_min_samples=30, now=1000.0)
    check("брокер" in r, "Победил прямой ответ брокера", r)

    # Брокер молчит — остаётся замершая цена
    r = mh.market_block_reason("EURUSD", trade_mode=4, spread_points=100,
                               dead_seconds=90, thin_ratio=2.5,
                               thin_min_samples=30, now=1000.0)
    check("не обновлялась" in r, "Дальше — замершая цена", r)

    # Цена живая — остаётся неликвидность
    mh.note_quote("EURUSD", 112, now=1000.0)
    r = mh.market_block_reason("EURUSD", trade_mode=4, spread_points=100,
                               dead_seconds=90, thin_ratio=2.5,
                               thin_min_samples=30, now=1001.0)
    check("неликвиден" in r, "И только потом наша оценка ликвидности", r)

    # Всё в порядке — пусто
    r = mh.market_block_reason("EURUSD", trade_mode=4, spread_points=10,
                               dead_seconds=90, thin_ratio=2.5,
                               thin_min_samples=30, now=1001.0)
    check(r == "", "Рынок нормальный — препятствий нет", r)


# =====================================================================
# ВСТРАИВАНИЕ В ТОРГОВЫЙ ЦИКЛ
# =====================================================================
def _process_symbol_src() -> str:
    src = (APP / "main.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "process_symbol")
    return ast.get_source_segment(src, fn) or ""


def test_profile_cannot_switch_it_off() -> None:
    """ГЛАВНОЕ. Профиль «Истеричка» (ignore_soft_filters = True) выключает
    rollover_guard_ok, volatility_ok и spread_cost_ok. Именно на этом профиле
    и получены ночные убытки. Позволить ему отменить и эту проверку значило
    бы написать её впустую."""
    print("\n[Профиль «Истеричка» не может отменить проверку]")
    src = _process_symbol_src()
    check("market_hours.market_block_reason" in src,
          "Проверка вызывается из торгового цикла")

    # Берём сам вызов и смотрим, не привязан ли он к ignore_soft_filters
    tree = ast.parse(src)
    call = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "market_block_reason"), None)
    check(call is not None, "Вызов найден")
    if call is not None:
        args_src = ast.dump(call)
        check("ignore_soft_filters" not in args_src,
              "Профиль не передаётся в проверку вовсе")

    # И условие вокруг вызова тоже не должно смотреть на профиль.
    # Заодно проверяем, что условие вообще ЖИВОЕ: `if False:` оставил бы вызов
    # в тексте, и поиск по строке этого не заметил бы.
    guard = next((n for n in ast.walk(tree)
                  if isinstance(n, ast.If)
                  and "market_block_reason" in ast.dump(n)), None)
    check(guard is not None, "Условие вокруг проверки найдено")
    if guard is not None:
        test_src = ast.dump(guard.test)
        check("ignore_soft_filters" not in test_src,
              "Условие вокруг проверки не смотрит на профиль")
        check("USE_MARKET_CLOSED_GUARD" in test_src,
              "И это настоящая настройка, а не отключённая наглухо ветка",
              test_src[:120])

        # Найденная причина обязана ЗАПРЕЩАТЬ вход, а не просто вычисляться.
        # Без этой проверки `if False:` вокруг запрета прошёл бы незамеченным:
        # причина считается, в журнал попадает, а сделка всё равно открывается.
        blocked = False
        for inner in ast.walk(guard):
            if not isinstance(inner, ast.If) or inner is guard:
                continue
            if not isinstance(inner.test, ast.Name):
                continue          # условие-константа запретом не является
            body = ast.dump(ast.Module(body=inner.body, type_ignores=[]))
            if "Return" in body and "last_reject_reason" in body:
                blocked = True
        check(blocked,
              "Найденная причина закрывает вход: выход из функции и "
              "объяснение человеку")

    # Для сравнения: ролловерная пауза профилем отменяется — это известно и
    # описано в config.py. Проверяем, что предупреждение на месте.
    example = (APP / "config.py.example").read_text(encoding="utf-8")
    check("ignore_soft_filters" in example and "Истеричка" in example,
          "В настройках предупреждено, что профиль отключает часть проверок")


def test_open_positions_are_not_touched() -> None:
    """Запрещается только ВХОД. Открытые сделки должны продолжать вестись."""
    print("\n[Открытые сделки не трогаются]")
    src = _process_symbol_src()
    manage_at = src.index("manage_open_positions")
    guard_at = src.index("market_block_reason")
    check(manage_at < guard_at,
          "Ведение открытых сделок стоит РАНЬШЕ запрета входа — "
          "иначе запрет отключал бы трейлинг")


def test_measurements_happen_every_pass() -> None:
    """Замеры должны копиться всегда, иначе к моменту, когда они понадобятся,
    сравнивать будет не с чем."""
    print("\n[Замеры делаются на каждом проходе, до фильтров]")
    src = _process_symbol_src()
    check("market_hours.note_spread" in src, "Спред замеряется")
    check("market_hours.note_quote" in src, "Отметка котировки записывается")
    check(src.index("note_spread") < src.index("market_block_reason"),
          "Замер идёт ДО проверки")
    # Ранние выходы из функции не должны стоять перед замером
    before = src[:src.index("note_spread")]
    check("return" not in before,
          "До замера нет ни одного выхода из функции — иначе на «плохих» "
          "проходах статистика не копилась бы", before[-200:])


def test_global_position_cap() -> None:
    print("\n[Общий потолок числа одновременных сделок]")
    src = _process_symbol_src()
    check("MAX_SIMULTANEOUS_POSITIONS" in src, "Потолок есть")
    check("count_open_positions(None" in src,
          "Считаются сделки по ВСЕМ парам, а не по одной")

    # Потолок обязан РАБОТАТЬ, а не просто присутствовать в тексте: должно
    # быть живое сравнение и выход из функции.
    tree = ast.parse(src)

    def live_if(names: set, needs_return: bool, without: set = frozenset()) -> bool:
        """Есть ли ЖИВОЕ условие (сравнение, а не константа) по этим именам.

        `without` отсекает соседнее условие, где встречаются те же имена:
        без него проверка «включён ли потолок» (max_all > 0) удовлетворялась
        бы внутренним «достигнут ли потолок» (open_now >= max_all), и
        отключение внешнего условия прошло бы незамеченным."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
                continue
            used = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
            if not names <= used or (used & without):
                continue
            if not needs_return:
                return True
            body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
            if "Return" in body and "last_reject_reason" in body:
                return True
        return False

    check(live_if({"max_all"}, needs_return=False, without={"open_now"}),
          "Потолок вообще проверяется (условие живое, а не отключено)")
    check(live_if({"open_now", "max_all"}, needs_return=True),
          "При достижении потолка вход закрывается с объяснением, "
          "а не просто считается")
    check(CFG.MAX_SIMULTANEOUS_POSITIONS == 0,
          "По умолчанию выключен — владелец предпочитает больше сделок",
          str(CFG.MAX_SIMULTANEOUS_POSITIONS))


def test_settings_reach_installed_program() -> None:
    print("\n[Настройки доезжают до уже установленной программы]")
    entry = [e for e in cm.ONE_TIME if e[0] == "MIGRATED_MARKET_CLOSED_GUARD"]
    check(bool(entry), "Одноразовая правка существует")
    if not entry:
        return
    changes = entry[0][1]
    check(changes.get("USE_MARKET_CLOSED_GUARD") is True, "Проверка включается")
    check(changes.get("USE_THIN_MARKET_GUARD") is True, "И оценка ликвидности")
    for key in ("MARKET_DEAD_SECONDS", "THIN_SPREAD_RATIO", "THIN_MIN_SAMPLES"):
        check(key in changes, f"Переносится {key}")
        check(getattr(CFG, key) == changes[key],
              f"И совпадает с эталоном: {key}",
              f"{getattr(CFG, key)} против {changes[key]}")


def test_money_target_scales_with_deposit() -> None:
    """Денежная цель прибыли обязана расти вместе со счётом.

    Владелец сообщил, что депозит будет $500-1000 вместо $65. Абсолютные
    суммы в настройках такую перемену не переживают: у «Истерички» цель
    записана как 1.0 доллара — на $65 это полтора процента счёта, а на $1000
    одна десятая. Чем больше счёт, тем больше объём при том же проценте
    риска, тем меньше движения цены нужно на «заработать доллар», и цель
    сжимается до пары пунктов, которые съедает спред."""
    print("\n[Цель прибыли считается от счёта, а не в долларах]")
    import risk_manager as rm

    profile = {"target_profit_money": 1.0}
    saved = getattr(CFG, "TARGET_PROFIT_PERCENT_OF_EQUITY", 0)
    try:
        CFG.TARGET_PROFIT_PERCENT_OF_EQUITY = 0.5

        small = rm.effective_target_money(profile, 65.0)
        big = rm.effective_target_money(profile, 1000.0)
        check(big > small, "На большем счёте цель больше",
              f"{small:.2f} -> {big:.2f}")
        check(abs(big - 5.0) < 1e-9, "На $1000 это 0.5% = 5 долларов", str(big))

        # Никогда не МЕНЬШЕ прежнего абсолютного числа: на маленьком счёте
        # доля процента может оказаться меньше цены одного пункта.
        check(small >= 1.0,
              "На маленьком счёте цель не опускается ниже прежней", str(small))
        check(rm.effective_target_money(profile, 10.0) == 1.0,
              "На совсем маленьком — ровно прежняя")

        # Выключение возвращает старое поведение
        CFG.TARGET_PROFIT_PERCENT_OF_EQUITY = 0
        check(rm.effective_target_money(profile, 1000.0) == 1.0,
              "0 = вернуться к абсолютному числу из профиля")

        # Мусор на входе не роняет расчёт
        CFG.TARGET_PROFIT_PERCENT_OF_EQUITY = 0.5
        check(rm.effective_target_money(profile, 0) == 1.0,
              "Счёт неизвестен (0) — берём абсолютное число")
        check(rm.effective_target_money({}, 1000.0) == 5.0,
              "Профиль без абсолютного числа — считаем от счёта")
    finally:
        CFG.TARGET_PROFIT_PERCENT_OF_EQUITY = saved

    # И это должно быть подключено, а не лежать без дела
    src = (APP / "main.py").read_text(encoding="utf-8")
    check("rm.effective_target_money(profile, equity)" in src,
          "Торговый цикл берёт цель через этот расчёт")
    check('profile["target_profit_money"], atr_value' not in src,
          "Абсолютное число больше не передаётся напрямую")


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ: РЫНОК ЗАКРЫТ ИЛИ НЕЛИКВИДЕН")
    print("=" * 62)

    test_timezone_never_enters_the_math()
    test_frozen_reason()
    test_broker_says_closed()
    test_normal_is_calm_market_not_average_day()
    test_sustained_wide_spread_is_caught()
    test_baseline_is_throttled_to_once_a_minute()
    test_restart_does_not_disable_the_guard()
    test_seeding_never_overwrites_live_data()
    test_seeding_is_wired_at_startup()
    test_no_verdict_without_enough_history()
    test_baseline_survives_restart()
    test_thin_market()
    test_silence_when_we_do_not_know()
    test_priority_of_reasons()

    test_profile_cannot_switch_it_off()
    test_open_positions_are_not_touched()
    test_measurements_happen_every_pass()
    test_global_position_cap()
    test_settings_reach_installed_program()
    test_money_target_scales_with_deposit()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
