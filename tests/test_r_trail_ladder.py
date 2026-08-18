#!/usr/bin/env python3
"""Тесты лестницы трейлинга в единицах риска сделки (R) и ускоренного
поджима тейк-профита.

ОТКУДА ЗАДАЧА. Владелец прислал отчёт MT5 за 06-07.08.2026 (211 сделок,
счёт $65, итог -19.99) и попросил: «сделай чтобы подтягивал трейлинг стоп,
стоп лосс» и «и тейк профит, закрывать чаще плюсовые сделки».

Разбор отчёта дал ровно одну развилку:

    стоп успел уйти в плюс  ->  74 сделки, +89.56
    стоп остался в минусе   -> 137 сделок, -109.55

Живут обе группы одинаково (медиана 6.6 и 6.5 минуты), то есть дело не в
длительности. Весь минус счёта — это сделки, где защита не успела включиться,
а включалась она не раньше 0.67R (безубыток), 0.80R (ATR-трейлинг) и 1.00R
(Profit Lock).

ЧТО ПРОВЕРЯЕМ ЗДЕСЬ. Не «функция что-то вернула», а свойства, ради которых
она написана: защита включается РАНЬШЕ прежних 0.67R; стоп никогда не уходит
назад; лестница не запирает больше, чем сделка стоила; настройки реально
доезжают до уже установленной программы.

Запуск:  python3 tests/test_r_trail_ladder.py
"""

from __future__ import annotations

import ast
import os
import sys
import tempfile
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
        print(f"  СБОЙ {name}" + (f"  -> {detail}" if detail else ""))


cfg = types.ModuleType("config")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg
CFG = cfg


class _FakeMT5(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return name


_mt5 = _FakeMT5("MetaTrader5")
_mt5.initialize = lambda *a, **k: False
_mt5.symbol_info = lambda *a, **k: None
_mt5.symbol_info_tick = lambda *a, **k: None
sys.modules["MetaTrader5"] = _mt5

import config_migrate as cm       # noqa: E402
import trade_manager as tm        # noqa: E402

LADDER = CFG.R_TRAIL_LADDER


def lock(peak_r: float, risk: float = 100.0, giveback: float = 0.0):
    """Удобная обёртка: пик задаём в R, ответ тоже переводим в R."""
    pts = tm.r_ladder_lock_points(peak_r * risk, risk, LADDER, giveback)
    return None if pts is None else pts / risk


# =====================================================================
# ГЛАВНОЕ СВОЙСТВО: защита включается раньше, чем раньше
# =====================================================================
def test_protection_starts_earlier_than_before() -> None:
    print("\n[Защита включается раньше прежних 0.67R]")

    # Прежние пороги (см. config.py.example): безубыток 1.0 ATR при поле стопа
    # 1.5 ATR = 0.67R; ATR-трейлинг 1.2 ATR = 0.80R; Profit Lock 1.00R.
    OLD_BREAK_EVEN_R = 1.0 / 1.5

    first_trigger = min(float(step[0]) for step in LADDER)
    check(first_trigger < OLD_BREAK_EVEN_R,
          "Первая ступень срабатывает раньше прежнего безубытка",
          f"{first_trigger} против {OLD_BREAK_EVEN_R:.2f}")

    check(lock(first_trigger - 0.01) is None,
          "До первой ступени стоп не трогаем вовсе")
    check(lock(first_trigger) is not None,
          "На первой ступени защита включается")

    # Именно ради этого всё и делалось: сделка, дошедшая до 0.5R и
    # развернувшаяся, раньше теряла ПОЛНЫЙ стоп (-1R). Теперь её риск
    # урезан. НЕ до нуля — это осознанное решение, см. следующий тест.
    check(lock(0.5) is not None and lock(0.5) > -1.0,
          "Сделка на +0.5R уже защищена (раньше теряла полный стоп)",
          str(lock(0.5)))
    check(lock(0.5) is not None and lock(0.5) <= -0.25,
          "Но стоп НЕ прижат к цене входа — сделке оставлено место",
          str(lock(0.5)))


def test_first_step_reduces_risk_but_is_not_break_even() -> None:
    """ЧИСЛА ЗДЕСЬ ПОМЕНЯЛИСЬ, И ЭТО НЕ ОПЕЧАТКА.

    Раньше первая ступень ставила стоп РОВНО в безубыток на +0.30R. Замер
    показал, чем это оборачивается: через эту ступень проходило 48.8% всех
    сделок, и после неё сделка уже не могла принести больше нуля, если
    движение не продолжалось. Прибыль срезалась в самом начале.

    Теперь первая ступень только УРЕЗАЕТ риск и не прижимает стоп к цене
    входа. Проверено на EURUSD и XAUUSD, на TRAIN, VALIDATION и OOS — итог
    улучшился во всех шести сочетаниях (см. docs/EXIT_VERSIONS.md)."""
    print("\n[Первая ступень урезает риск, а не ставит безубыток]")
    first_trigger, first_lock = min(LADDER, key=lambda s: float(s[0]))
    check(float(first_lock) < 0.0,
          "Первая ступень урезает риск, а не запирает ноль",
          str(first_lock))
    check(float(first_lock) > -1.0,
          "Но риск действительно урезан — стоп ближе исходного",
          str(first_lock))
    # Запирать прибыль сразу нельзя: стоп встал бы слишком близко к цене и
    # выбивался обычным шумом, а сделка не успевала бы развернуться в плюс.
    check(float(first_trigger) > 0,
          "Включается не мгновенно, а пройдя часть риска")
    check(float(first_trigger) >= 0.5,
          "И не раньше половины риска: до этого не вмешиваемся вовсе",
          str(first_trigger))

    # Безубыток никуда не делся — он просто стал ВТОРОЙ ступенью.
    нули = [float(з) for _, з in LADDER if abs(float(з)) < 1e-9]
    check(len(нули) == 1, "Безубыток в лестнице есть, ровно один раз")


def test_ladder_never_locks_more_than_earned() -> None:
    print("\n[Нельзя запереть больше, чем сделка стоила]")
    for peak_r in (0.3, 0.45, 0.6, 0.99, 1.0, 1.4, 1.5, 2.4, 2.5, 5.0):
        got = lock(peak_r)
        if got is None:
            continue
        check(got <= peak_r + 1e-9,
              f"Пик {peak_r}R: заперто {got:.2f}R, не больше пика")
    # Иначе стоп оказался бы ВПЕРЕДИ цены и сработал бы мгновенно по худшей
    # стороне — то есть «защита» сама закрывала бы сделку в минус.
    #
    # На штатной лестнице это не проверить: там каждая ступень запирает меньше
    # своего порога, и зажим просто никогда не нужен. А ЛЕСТНИЦУ ЧЕЛОВЕК
    # ПРАВИТ РУКАМИ в config.py — вот такой случай и важен.
    broken = [(0.5, 2.0), (1.0, 5.0)]      # запирает больше, чем сделка стоила
    got = tm.r_ladder_lock_points(60.0, 100.0, broken, 0.0)   # пик 0.6R
    check(got is not None and got <= 60.0 + 1e-9,
          "Кривая ступень (0.5R -> 2.0R) зажимается по пику, а не ставит стоп впереди цены",
          str(got))
    got2 = tm.r_ladder_lock_points(150.0, 100.0, broken, 0.0)  # пик 1.5R
    check(got2 is not None and got2 <= 150.0 + 1e-9,
          "И на верхней кривой ступени тоже", str(got2))


def test_ladder_only_grows() -> None:
    print("\n[Чем выше пик, тем больше заперто — и никогда наоборот]")
    prev = -1.0
    for peak_r in [i / 20.0 for i in range(0, 80)]:
        got = lock(peak_r)
        if got is None:
            continue
        check_ok = got >= prev - 1e-9
        if not check_ok:
            check(False, f"Пик {peak_r}R не должен снижать фиксацию",
                  f"{prev:.3f} -> {got:.3f}")
            return
        prev = got
    check(True, "Фиксация только растёт на всём диапазоне пика 0..4R")


def test_giveback_tightens_but_never_loosens() -> None:
    print("\n[Отступ от пика может только ПОДНЯТЬ фиксацию]")
    for peak_r in (0.3, 0.6, 1.0, 1.5, 2.0, 3.0, 6.0):
        plain = lock(peak_r, giveback=0.0)
        with_gb = lock(peak_r, giveback=CFG.R_TRAIL_GIVEBACK_R)
        if plain is None:
            check(with_gb is None,
                  f"Пик {peak_r}R: без ступени отступ ничего не включает")
            continue
        check(with_gb >= plain - 1e-9,
              f"Пик {peak_r}R: отступ не ослабляет фиксацию",
              f"{plain:.2f} -> {with_gb:.2f}")

    # На сильном движении отступ обязан обгонять ступеньки, иначе между
    # 2.5R и следующей ступенькой стоп стоял бы на месте, отдавая весь ход.
    far = lock(6.0, giveback=CFG.R_TRAIL_GIVEBACK_R)
    top_lock = max(float(s[1]) for s in LADDER)
    check(far > top_lock,
          "За верхней ступенью стоп ведёт отступ от пика, а не последняя ступень",
          f"{far} против {top_lock}")
    check(abs(far - (6.0 - CFG.R_TRAIL_GIVEBACK_R)) < 1e-9,
          "И отстаёт от пика ровно на заданный отступ")


def test_disabled_and_broken_input() -> None:
    print("\n[Выключено и мусор на входе]")
    check(tm.r_ladder_lock_points(1.0, 0.0, LADDER, 0.5) is None,
          "Риск сделки неизвестен (0) — лестница молчит")
    check(tm.r_ladder_lock_points(1.0, -5.0, LADDER, 0.5) is None,
          "Отрицательный риск — лестница молчит")
    check(tm.r_ladder_lock_points(100.0, 100.0, [], 0.5) is None,
          "Пустая лестница — молчит, а не запирает наугад")
    check(tm.r_ladder_lock_points(100.0, 100.0, None, 0.5) is not None,
          "None = взять лестницу из настроек (там она есть)")
    # Убыточная сделка не должна получать «защиту» вообще: пик в минусе.
    check(tm.r_ladder_lock_points(-50.0, 100.0, LADDER, 0.5) is None,
          "Пик в минусе — защита не включается")


def test_ladder_is_sorted_by_meaning_not_by_luck() -> None:
    print("\n[Порядок ступеней в настройке не важен]")
    shuffled = list(reversed(LADDER))
    for peak_r in (0.3, 0.7, 1.2, 2.0, 3.0):
        a = tm.r_ladder_lock_points(peak_r * 100, 100.0, LADDER, 0.0)
        b = tm.r_ladder_lock_points(peak_r * 100, 100.0, shuffled, 0.0)
        check(a == b, f"Пик {peak_r}R: перемешанные ступени дают тот же ответ",
              f"{a} против {b}")


# =====================================================================
# ВСТРАИВАНИЕ В УПРАВЛЕНИЕ ПОЗИЦИЯМИ
# =====================================================================
def _managed_source() -> str:
    body = (APP / "trade_manager.py").read_text(encoding="utf-8")
    tree = ast.parse(body)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "manage_open_positions":
            return ast.get_source_segment(body, node) or ""
    return ""


def test_ladder_is_wired_into_position_management() -> None:
    print("\n[Лестница реально применяется к открытым сделкам]")
    src = _managed_source()
    check("r_ladder_lock_points" in src,
          "manage_open_positions вызывает лестницу")
    check("USE_R_TRAIL_LADDER" in src,
          "И её можно выключить настройкой")

    # Ключевое: результат обязан пройти через _better_sl. Именно он и есть
    # запрет двигать стоп назад — без него лестница могла бы РАСШИРИТЬ стоп
    # и превратить защиту в свою противоположность.
    # Раньше здесь искалось прямое обращение к _better_sl. Теперь все четыре
    # механизма ходят через общую точку `предложить`, а она внутри вызывает
    # тот же _better_sl. Проверяем и то, и другое: важно не имя вызова, а
    # что стоп не может уехать назад.
    tail = src.split("r_ladder_lock_points", 1)[1][:900]
    check("предложить(" in tail or "_better_sl" in tail,
          "Стоп от лестницы проходит через общую точку переноса")
    точка = src.split("def предложить", 1)
    check(len(точка) > 1 and "_better_sl" in точка[1][:400],
          "А она двигает стоп только через _better_sl (никогда назад)")


def test_better_sl_never_moves_stop_backwards() -> None:
    print("\n[_better_sl не двигает стоп назад — на этом держится всё]")
    check(tm._better_sl(True, 1.2000, 1.1990) == 1.2000,
          "Покупка: стоп не опускается")
    check(tm._better_sl(True, 1.2000, 1.2010) == 1.2010,
          "Покупка: стоп поднимается")
    check(tm._better_sl(False, 1.2000, 1.2010) == 1.2000,
          "Продажа: стоп не поднимается")
    check(tm._better_sl(False, 1.2000, 1.1990) == 1.1990,
          "Продажа: стоп опускается")


# =====================================================================
# ТЕЙК-ПРОФИТ: ЧАЩЕ ЗАКРЫВАТЬ ПЛЮСОВЫЕ
# =====================================================================
def test_take_profit_tightens_faster() -> None:
    print("\n[Цель прибыли поджимается быстрее]")
    check(CFG.TP_TIGHTEN_SHRINK_PER_MINUTE > 0.10,
          "Скорость поджима выросла против прежних 10% в минуту",
          str(CFG.TP_TIGHTEN_SHRINK_PER_MINUTE))

    # Медиана жизни сделки в отчёте — 6.5 минуты. Прежняя скорость ужимала
    # цель за это время лишь примерно вдвое; просьба была «закрывать чаще».
    start = 100.0
    old = tm.shrunk_target_points(start, 6.5 * 60, 0.10, CFG.TP_TIGHTEN_MIN_FRACTION)
    new = tm.shrunk_target_points(start, 6.5 * 60,
                                  CFG.TP_TIGHTEN_SHRINK_PER_MINUTE,
                                  CFG.TP_TIGHTEN_MIN_FRACTION)
    check(new < old,
          "За типичные 6.5 минуты цель теперь ближе, чем была",
          f"{new:.1f} против {old:.1f}")


def test_take_profit_floor_survives() -> None:
    print("\n[Но цель НИКОГДА не становится меньше своего стопа]")
    check(CFG.TP_TIGHTEN_MIN_R >= 1.0,
          "Пол в 1R на месте", str(CFG.TP_TIGHTEN_MIN_R))

    # Почему это не ослаблено вместе со скоростью: в отчёте сделки, у которых
    # цель стояла БЛИЖЕ стопа, дали -2.53 при винрейте 43%. При таком винрейте
    # выигрыш обязан быть больше проигрыша, иначе минус даёт арифметика.
    risk = 200.0
    target = tm.shrunk_target_points(50.0, 99999, 0.9, 0.01)
    check(target < risk, "Сжатие само по себе может увести цель ниже риска")

    guarded = max(target, risk * CFG.TP_TIGHTEN_MIN_R)
    check(guarded >= risk,
          "А с полом 1R цель всегда покрывает хотя бы собственный стоп")

    body = (APP / "trade_manager.py").read_text(encoding="utf-8")
    check("min_r" in body and "risk_points * min_r" in body,
          "Пол применяется в коде, а не только описан в настройках")


# =====================================================================
# НАСТРОЙКИ ДОЛЖНЫ ДОЕХАТЬ ДО УЖЕ УСТАНОВЛЕННОЙ ПРОГРАММЫ
# =====================================================================
def test_migration_carries_the_whole_ladder() -> None:
    print("\n[Лестница доезжает до уже установленного config.py]")
    entry = [e for e in cm.ONE_TIME if e[0] == "MIGRATED_R_TRAIL_LADDER"]
    check(bool(entry), "Одноразовая правка существует")
    if not entry:
        return
    changes = entry[0][1]

    # САМОЕ ВАЖНОЕ. build_patch намеренно не переносит многострочные значения,
    # а R_TRAIL_LADDER в эталоне записан многострочным. Без него у человека
    # оказался бы USE_R_TRAIL_LADDER = True и НИ ОДНОЙ ступени — трейлинг
    # молча не делал бы ничего, и это выглядело бы как «опять не работает».
    check("R_TRAIL_LADDER" in changes,
          "Сама лестница переносится, а не только выключатель")
    check(changes.get("USE_R_TRAIL_LADDER") is True,
          "Выключатель включён")
    check("R_TRAIL_GIVEBACK_R" in changes, "Отступ от пика переносится")

    example = (APP / "config.py.example").read_text(encoding="utf-8")
    ladder_src = ""
    for node in ast.parse(example).body:
        if (isinstance(node, ast.Assign) and node.targets
                and getattr(node.targets[0], "id", "") == "R_TRAIL_LADDER"):
            ladder_src = ast.get_source_segment(example, node) or ""
    check("\n" in ladder_src,
          "В эталоне лестница действительно многострочная (иначе проверка выше пуста)")
    check([tuple(x) for x in changes["R_TRAIL_LADDER"]] == [tuple(x) for x in LADDER],
          "И совпадает с эталоном — иначе у людей были бы разные лестницы",
          f"{changes['R_TRAIL_LADDER']} против {LADDER}")


def test_migration_applies_once_to_a_real_file() -> None:
    print("\n[Правка применяется к настоящему файлу и ровно один раз]")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "config.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write("USE_TRAILING_STOP = True\nTP_TIGHTEN_SHRINK_PER_MINUTE = 0.10\n")

        notes = cm.apply_one_time(path)
        text = open(path, encoding="utf-8").read()
        applied = types.ModuleType("t")
        exec(text, applied.__dict__)

        check(applied.USE_R_TRAIL_LADDER is True, "Выключатель записан в файл")
        check(len(applied.R_TRAIL_LADDER) == len(LADDER),
              "Ступени записаны в файл целиком", str(applied.R_TRAIL_LADDER))
        check(applied.TP_TIGHTEN_SHRINK_PER_MINUTE == 0.18,
              "Скорость поджима тейка переписана со старых 0.10",
              str(applied.TP_TIGHTEN_SHRINK_PER_MINUTE))
        check(any("стоп" in n for n in notes), "Человеку объяснили, что изменилось")

        # Второй раз — ничего: человек мог сам поправить значения, и
        # переписывать их при каждом запуске нельзя.
        with open(path, "a", encoding="utf-8") as f:
            f.write("\nUSE_R_TRAIL_LADDER = False\n")
        cm.apply_one_time(path)
        again = types.ModuleType("t2")
        exec(open(path, encoding="utf-8").read(), again.__dict__)
        check(again.USE_R_TRAIL_LADDER is False,
              "Отключено человеком — повторно не включаем")


def test_migration_survives_multiline_value() -> None:
    print("\n[Многострочная настройка заменяется целиком, а не первой строкой]")
    # Мина, на которой это уже подорвалось. Замена шла построчной регуляркой:
    # первая строка `R_TRAIL_LADDER = [` менялась на новое значение, а хвост
    # `    (0.30, 0.00),` оставался висеть отдельным куском с отступом.
    # config.py после такой «миграции» не разбирался вообще — IndentationError,
    # то есть человек терял ВСЕ свои настройки разом.
    text = (
        "USE_TRAILING_STOP = True\n"
        "R_TRAIL_LADDER = [\n"
        "    (0.90, 0.10),\n"
        "    (1.90, 0.70),\n"
        "]\n"
        "TP_TIGHTEN_MIN_R = 1.0\n"
    )
    out = cm._replace_or_append(text, "R_TRAIL_LADDER", repr([(0.3, 0.0)]))
    check("(0.90, 0.10)" not in out,
          "Старые ступени убраны полностью", out)
    try:
        module = types.ModuleType("m")
        exec(out, module.__dict__)
        parsed = True
    except SyntaxError as e:
        parsed = False
        module = None
        check(False, "Файл после замены разбирается", str(e))
    if parsed:
        check(True, "Файл после замены разбирается")
        check(module.R_TRAIL_LADDER == [(0.3, 0.0)], "Новое значение на месте")
        check(module.TP_TIGHTEN_MIN_R == 1.0, "Соседние настройки не задеты")

    # И полный проход по эталону не должен ломать файл: именно так это и
    # вылезло — apply_one_time поверх свежей копии config.py.example.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "config.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write((APP / "config.py.example").read_text(encoding="utf-8"))
        cm.apply_one_time(path)
        try:
            exec(open(path, encoding="utf-8").read(), types.ModuleType("z").__dict__)
            check(True, "Миграция поверх эталона оставляет рабочий config.py")
        except SyntaxError as e:
            check(False, "Миграция поверх эталона оставляет рабочий config.py", str(e))


def test_ladder_values_are_sane() -> None:
    print("\n[Сама лестница осмысленна]")
    triggers = [float(s[0]) for s in LADDER]
    locks = [float(s[1]) for s in LADDER]
    check(len(set(triggers)) == len(triggers),
          "Нет двух ступеней с одним порогом")
    locks_in_order = [float(s[1]) for s in sorted(LADDER, key=lambda s: float(s[0]))]
    check(locks_in_order == sorted(locks_in_order),
          "Более высокий порог запирает не меньше низкого", str(locks_in_order))
    for t, l in LADDER:
        check(float(l) < float(t),
              f"Ступень {t}R запирает {l}R — меньше пика, иначе стоп впереди цены")
    check(CFG.R_TRAIL_GIVEBACK_R > 0,
          "Отступ от пика задан", str(CFG.R_TRAIL_GIVEBACK_R))
    # Отступ шире собственного стопа означал бы «отдать больше, чем рисковали»
    check(CFG.R_TRAIL_GIVEBACK_R < 1.0,
          "И он меньше 1R — отдавать больше своего риска бессмысленно")


# =====================================================================
# ПОТОЛОК ПЛЕЧА — выключен по решению владельца, но код обязан работать
# =====================================================================
def test_leverage_cap() -> None:
    print("\n[Потолок плеча: выключен по умолчанию, но работает при включении]")
    import risk_manager as rm
    import control as ctl
    from state import SymbolState

    check(CFG.MAX_POSITION_LEVERAGE == 0,
          "По умолчанию ВЫКЛЮЧЕН — владелец решил оставить крупные лоты",
          str(CFG.MAX_POSITION_LEVERAGE))

    class FX:
        volume_min = 0.01; volume_max = 100.0; volume_step = 0.01
        trade_tick_value = 1.0; trade_tick_size = 0.00001
        trade_contract_size = 100000.0

    saved_info, saved_price = rm._symbol_info, rm.mt5c.get_price
    saved_override = ctl.control.get_lot_override
    saved_lev = CFG.MAX_POSITION_LEVERAGE
    saved_cap = CFG.MAX_TRADE_RISK_PERCENT_OF_EQUITY
    rm._symbol_info = lambda s: FX()
    rm.mt5c.get_price = lambda s: 1.0        # 1 лот = 100 000 денег
    ctl.control.get_lot_override = lambda s: 0
    try:
        CFG.MAX_TRADE_RISK_PERCENT_OF_EQUITY = 0      # изучаем ТОЛЬКО плечо
        equity = 65.26                                 # счёт владельца
        # Стоп 0.5 пункта -> расчёт просит очень крупный лот, как ночью в отчёте
        CFG.MAX_POSITION_LEVERAGE = 0
        big = rm.calc_lot("USDCAD", 0.000005, equity, SymbolState("USDCAD"))

        CFG.MAX_POSITION_LEVERAGE = 100
        capped = rm.calc_lot("USDCAD", 0.000005, equity, SymbolState("USDCAD"))
        check(capped < big, "Включённый потолок уменьшает объём",
              f"{big} -> {capped}")
        check(capped * 100000.0 <= equity * 100 + 1e-6,
              "Позиция не превышает плечо 100:1", str(capped))
        # Не отмена сделки, а уменьшение: вход остаётся.
        check(capped > 0, "Сделка не отменяется, только уменьшается", str(capped))

        # На большом счёте потолок не мешает — он относительный.
        rich = rm.calc_lot("USDCAD", 0.000005, 100000.0, SymbolState("USDCAD"))
        check(rich > capped, "На большом счёте объём больше", str(rich))
    finally:
        rm._symbol_info = saved_info
        rm.mt5c.get_price = saved_price
        ctl.control.get_lot_override = saved_override
        CFG.MAX_POSITION_LEVERAGE = saved_lev
        CFG.MAX_TRADE_RISK_PERCENT_OF_EQUITY = saved_cap


def test_get_price_is_safe_without_connection() -> None:
    print("\n[Цена без связи с терминалом — ноль, а не падение]")
    import mt5_connector as mt5c
    check(mt5c.get_price("EURUSD") == 0.0,
          "Тика нет — возвращается 0.0")
    # Ноль обязателен: считать плечо от нулевой цены нельзя, и вызывающий
    # это проверяет (см. calc_lot).
    body = (APP / "risk_manager.py").read_text(encoding="utf-8")
    check("price > 0" in body,
          "calc_lot проверяет цену перед делением")


def main() -> int:
    test_protection_starts_earlier_than_before()
    test_first_step_reduces_risk_but_is_not_break_even()
    test_ladder_never_locks_more_than_earned()
    test_ladder_only_grows()
    test_giveback_tightens_but_never_loosens()
    test_disabled_and_broken_input()
    test_ladder_is_sorted_by_meaning_not_by_luck()

    test_ladder_is_wired_into_position_management()
    test_better_sl_never_moves_stop_backwards()

    test_take_profit_tightens_faster()
    test_take_profit_floor_survives()

    test_migration_carries_the_whole_ladder()
    test_migration_applies_once_to_a_real_file()
    test_migration_survives_multiline_value()
    test_ladder_values_are_sane()

    test_leverage_cap()
    test_get_price_is_safe_without_connection()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
