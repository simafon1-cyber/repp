"""scan_rotation.py — как обойти много пар и не растянуть круг.

ЗАЧЕМ
Владелец: «не хочу каждый раз вписывать пары. Пусть само подгрузится у
брокера и работает по всему, на чём можно заработать за день».

=====================================================================
ПОЧЕМУ РАНЬШЕ ПОЛУЧАЛОСЬ ТОЛЬКО ДВАДЦАТЬ ПАР
=====================================================================
Программа обходила КАЖДУЮ пару на КАЖДОМ проходе — раз в POLL_SECONDS = 5
секунд. Обход стоит примерно 40 мс на пару, поэтому на трёх сотнях пар круг
занимал бы двенадцать секунд вместо пяти.

Но вот что выяснилось при проверке: вход в сделку возможен ТОЛЬКО на новом
баре (см. is_new_bar в main.process_symbol), а рабочий таймфрейм — M5. Новый
бар появляется раз в 300 секунд. То есть из шестидесяти обходов пары между
барами пятьдесят девять заканчивались ничем: посчитали индикаторы и выбросили.

Потолок в двадцать пар был не настоящим. Он получался из того, что программа
делала одну и ту же работу шестьдесят раз подряд.

=====================================================================
ЧТО ДЕЛАЕТСЯ ВМЕСТО ЭТОГО
=====================================================================
Пары делятся на две части, и это деление — главное:

1. ПАРА С ОТКРЫТОЙ СДЕЛКОЙ обходится ВСЕГДА, на каждом проходе. Здесь живёт
   трейлинг-стоп, безубыток и аварийное закрытие — задержка тут недопустима,
   на этом теряются деньги.

2. ПАРА БЕЗ СДЕЛКИ обходится ПО ОЧЕРЕДИ. Весь список прокручивается за
   ROTATE_SECONDS (по умолчанию 30 секунд) — это десятая часть пятиминутного
   бара, то есть вход всё равно происходит в начале своего бара.

Сколько брать за проход — считается, а не задаётся на глаз:

      пар за проход = всего пар / (30 секунд / 5 секунд) = всего / 6

      120 пар -> 20 за проход -> 0.8 секунды на круг
      300 пар -> 50 за проход -> 2.0 секунды на круг

=====================================================================
И ГЛАВНОЕ: ПРОГРАММА СМОТРИТ НА ЧАСЫ, А НЕ НА МОЮ ОЦЕНКУ
=====================================================================
40 мс — это оценка. На медленном компьютере или медленном брокере она
окажется неправдой, а расплачиваться будет владелец растянутым кругом.

Поэтому программа замеряет, сколько круг занял НА САМОМ ДЕЛЕ, и если он вышел
за отведённую долю POLL_SECONDS — берёт на следующем проходе меньше пар.
Когда время есть — постепенно возвращает обратно. Никаких «должно хватить».
"""

import logging

log = logging.getLogger("scan_rotation")

# За сколько секунд обязан прокрутиться весь список. 30 секунд — десятая часть
# бара M5: вход происходит в начале своего бара, а не в конце.
ROTATE_SECONDS = 30.0

# Какую долю POLL_SECONDS позволено занимать обходу пар. Остальное оставлено
# на всё прочее в круге: позиции, настройки, дашборд.
BUDGET_FRACTION = 0.5

# Меньше этого за проход не опускаемся никогда: иначе при тормозящем терминале
# список перестал бы прокручиваться совсем.
MIN_SLICE = 2


def planned_slice(total: int, poll_seconds: float,
                  rotate_seconds: float = ROTATE_SECONDS) -> int:
    """Сколько пар брать за проход, чтобы обойти все за rotate_seconds."""
    try:
        count = int(total)
        poll = float(poll_seconds)
        rotate = float(rotate_seconds)
    except (TypeError, ValueError):
        return MIN_SLICE
    if count <= 0:
        return 0
    if poll <= 0 or rotate <= 0:
        return count
    passes = rotate / poll
    if passes <= 1:
        return count
    size = int(count / passes)
    if size * passes < count:      # округляем вверх: остаток тоже надо обойти
        size += 1
    return max(MIN_SLICE, min(count, size))


def adjust_slice(current: int, last_pass_seconds: float, poll_seconds: float,
                 total: int, budget_fraction: float = BUDGET_FRACTION) -> int:
    """Подправить размер порции по ФАКТИЧЕСКОМУ времени прошлого прохода.

    Вылезли за бюджет — режем пропорционально перерасходу (сразу, потому что
    растянутый круг вредит уже сейчас). Уложились с запасом — прибавляем по
    одной паре (осторожно, потому что спешить некуда)."""
    try:
        size = int(current)
        spent = float(last_pass_seconds)
        poll = float(poll_seconds)
        count = int(total)
        fraction = float(budget_fraction)
    except (TypeError, ValueError):
        return max(MIN_SLICE, int(current or MIN_SLICE))

    if count <= 0:
        return 0
    size = max(MIN_SLICE, min(size, count))
    if poll <= 0 or fraction <= 0 or spent < 0:
        return size

    budget = poll * fraction
    if spent > budget:
        shrunk = int(size * budget / spent) if spent > 0 else MIN_SLICE
        return max(MIN_SLICE, min(size - 1, shrunk))
    if spent < budget * 0.5 and size < count:
        return size + 1
    return size


def plan(symbols: list, busy: set, cursor: int, size: int) -> dict:
    """Кого обходить на этом проходе.

    Возвращает {"symbols": [...], "cursor": N}. Пары с открытой сделкой входят
    ВСЕГДА и очередь не тратят: их ведёт трейлинг-стоп, и пропуск прохода по
    ним стоит денег. Остальные берутся по кругу с позиции cursor."""
    all_syms = list(symbols or ())
    if not all_syms:
        return {"symbols": [], "cursor": 0}

    busy = set(busy or ())
    always = [s for s in all_syms if s in busy]
    rest = [s for s in all_syms if s not in busy]

    if not rest:
        return {"symbols": always, "cursor": 0}

    take = max(0, min(int(size or 0), len(rest)))
    if take <= 0:
        return {"symbols": always, "cursor": int(cursor or 0) % len(rest)}

    start = int(cursor or 0) % len(rest)
    picked = [rest[(start + i) % len(rest)] for i in range(take)]
    return {"symbols": always + picked, "cursor": (start + take) % len(rest)}


def describe(total: int, size: int, poll_seconds: float) -> str:
    """Одна строка для журнала: как часто на деле проверяется каждая пара."""
    if total <= 0 or size <= 0 or poll_seconds <= 0:
        return "Пары не обходятся."
    passes = (total + size - 1) // size
    every = passes * float(poll_seconds)
    return (f"Пар в работе: {total}. За проход обходится {size}, "
            f"каждая пара проверяется раз в {every:.0f} с.")
