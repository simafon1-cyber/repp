"""
cloud_journal.py — журнал сделок в облаке (в вашем закрытом репозитории
GitHub), чтобы историю можно было посмотреть в любой момент и с любого
устройства, даже когда компьютер с ботом выключен.

ЗАЧЕМ
Журнал сделок лежит на рабочем компьютере в trades_log.csv, а настоящая
история — внутри терминала MetaTrader. Ни то, ни другое не видно снаружи.
Разобрать «почему одни минуса» по скриншоту нельзя: на картинке нет ни
времени жизни сделки, ни расстояния до стопа. Этот модуль раз в N минут
выкладывает три файла в папку journal/ вашего репозитория:

  journal/trades_<счёт>.csv    — что делал бот (вход, стоп, тейк, причина)
  journal/history_<счёт>.csv   — реальные закрытые сделки из MetaTrader
  journal/summary_<счёт>.md    — разбор человеческим языком: винрейт,
                                 средний плюс/минус, сколько сделок умерло
                                 за секунды, по каким парам минус

ПОЧЕМУ ИМЕННО GitHub
Он уже используется программой для обновлений, ничего нового ставить не
нужно, история версий ведётся сама, а репозиторий закрытый — файлы видите
только вы и те, кому вы дали доступ.

ЧТО ТУДА НЕ ПОПАДАЕТ НИКОГДА
Пароль от счёта, пароль инвестора, ключи API, токены, файл сессии Telegram,
содержимое config.py. Выгружаются только сделки. Номер счёта и имя брокера
попадают — без них непонятно, чей это журнал; если это лишнее, поставьте
JOURNAL_MASK_ACCOUNT = True, и номер будет показан как ****1234.

ВЫКЛЮЧЕНО ПО УМОЛЧАНИЮ
Отправка чего-либо наружу не должна включаться сама. Нужно вписать токен и
поставить галочку на вкладке «Система».

ТОКЕН
GitHub -> Settings -> Developer settings -> Personal access tokens ->
Fine-grained -> ваш репозиторий -> Repository permissions -> Contents:
Read and write. Токен хранится в config.py и шифруется наравне с ключами API.
"""

import base64
import csv
import io
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request

import config as cfg

log = logging.getLogger("cloud_journal")

API = "https://api.github.com"

# Сколько секунд считать сделку "мгновенной". Сделка, закрытая быстрее, почти
# всегда означает стоп внутри рыночного шума: цена не успела никуда пойти,
# её мотнуло на спреде и выбило.
INSTANT_DEATH_SECONDS = 60


# =====================================================================
# НАСТРОЙКИ
# =====================================================================
def enabled() -> bool:
    return bool(getattr(cfg, "JOURNAL_CLOUD_ENABLED", False))


def repo() -> str:
    """owner/repo. По умолчанию тот же, откуда приходят обновления."""
    value = str(getattr(cfg, "JOURNAL_REPO", "") or "").strip().strip("/")
    if value:
        return value
    return str(getattr(cfg, "UPDATE_REPO", "") or "").strip().strip("/")


def branch() -> str:
    return str(getattr(cfg, "JOURNAL_BRANCH", "") or "main").strip() or "main"


def token() -> str:
    """Токен с правом ЗАПИСИ. Отдельный от токена обновлений: тому хватает
    чтения, и смешивать их — значит без нужды дать право записи туда, где оно
    не требуется."""
    return str(getattr(cfg, "JOURNAL_TOKEN", "") or "").strip()


def folder() -> str:
    return str(getattr(cfg, "JOURNAL_FOLDER", "") or "journal").strip("/") or "journal"


def upload_interval_seconds() -> float:
    try:
        minutes = float(getattr(cfg, "JOURNAL_UPLOAD_MINUTES", 15) or 0)
    except (TypeError, ValueError):
        minutes = 15.0
    return max(60.0, minutes * 60.0)


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def account_label(account) -> str:
    """Как называть файлы журнала. Номер счёта можно замаскировать."""
    text = str(account or "unknown")
    if getattr(cfg, "JOURNAL_MASK_ACCOUNT", False) and len(text) > 4:
        return "****" + text[-4:]
    return text


def ready() -> tuple:
    """(готово, причина). Причина — понятный текст, а не код ошибки."""
    if not enabled():
        return False, "Журнал в облаке выключен (вкладка «Система»)."
    if not repo() or "/" not in repo():
        return False, ("Не указан репозиторий. Впишите его в виде "
                       "владелец/название на вкладке «Система».")
    if not token():
        return False, ("Не указан токен GitHub с правом записи (Contents: "
                       "Read and write). Без него выкладывать некуда.")
    return True, ""


def explain_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return ("Репозиторий или ветка не найдены. Проверьте название и то, "
                    "что токен выдан именно на этот репозиторий.")
        if exc.code == 401:
            return "Токен не принят GitHub. Скорее всего он истёк — выпустите новый."
        if exc.code == 403:
            return ("Нет права записи. Токену нужен доступ Contents: "
                    "Read and write именно к этому репозиторию.")
        if exc.code == 409:
            return "Файл в облаке изменился одновременно с отправкой. Повторите."
        if exc.code == 422:
            return "GitHub отклонил запись: возможно, указана несуществующая ветка."
        return f"GitHub ответил ошибкой {exc.code}."
    if isinstance(exc, urllib.error.URLError):
        return f"Нет связи с GitHub: {exc.reason}"
    return f"{type(exc).__name__}: {exc}"


# =====================================================================
# GITHUB
# =====================================================================
def _request(method: str, url: str, payload: dict = None, timeout: int = 30):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "AI-Scalper-Journal",
        "Authorization": f"Bearer {token()}",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    return urllib.request.urlopen(req, timeout=timeout)


def remote_sha(path: str):
    """sha файла в репозитории или None, если его там нет. GitHub требует sha
    при перезаписи — так он убеждается, что вы меняете именно ту версию,
    которую видели."""
    url = f"{API}/repos/{repo()}/contents/{path}?ref={branch()}"
    try:
        with _request("GET", url) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data.get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def put_file(path: str, text: str, message: str) -> str:
    """Записать/обновить один файл. Возвращает короткий номер коммита."""
    payload = {
        "message": message,
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "branch": branch(),
    }
    sha = remote_sha(path)
    if sha:
        payload["sha"] = sha
    url = f"{API}/repos/{repo()}/contents/{path}"
    with _request("PUT", url, payload) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str((data.get("commit") or {}).get("sha", ""))[:12]


# =====================================================================
# РАЗБОР СДЕЛОК
# =====================================================================
def analyze(deals: list) -> dict:
    """Разбор закрытых сделок: что именно идёт не так.

    Считаем только по сделкам БОТА (is_bot): ручные сделки к его настройкам
    отношения не имеют, и смешивать их — значит чинить не ту проблему."""
    own = [d for d in deals or [] if d.get("is_bot", True)]
    result = {
        "trades": len(own),
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "net": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "payoff": 0.0,
        "instant_deaths": 0,
        "instant_death_pct": 0.0,
        "median_life_sec": None,
        "costs": 0.0,
        "by_symbol": {},
        "findings": [],
    }
    if not own:
        return result

    wins, losses, lives = [], [], []
    for d in own:
        profit = float(d.get("profit", 0) or 0)
        costs = float(d.get("commission", 0) or 0) + float(d.get("swap", 0) or 0)
        result["costs"] += costs
        (wins if profit >= 0 else losses).append(profit)
        life = d.get("duration_sec")
        if life is not None:
            lives.append(int(life))
            if int(life) <= INSTANT_DEATH_SECONDS:
                result["instant_deaths"] += 1
        sym = result["by_symbol"].setdefault(
            d.get("symbol", "?"), {"trades": 0, "net": 0.0, "wins": 0})
        sym["trades"] += 1
        sym["net"] += profit
        if profit >= 0:
            sym["wins"] += 1

    result["wins"] = len(wins)
    result["losses"] = len(losses)
    result["win_rate"] = round(len(wins) / len(own) * 100.0, 1)
    result["gross_profit"] = round(sum(wins), 2)
    result["gross_loss"] = round(sum(losses), 2)
    result["net"] = round(sum(wins) + sum(losses), 2)
    result["costs"] = round(result["costs"], 2)
    result["avg_win"] = round(sum(wins) / len(wins), 2) if wins else 0.0
    result["avg_loss"] = round(sum(losses) / len(losses), 2) if losses else 0.0
    if result["avg_loss"]:
        result["payoff"] = round(abs(result["avg_win"] / result["avg_loss"]), 2)
    if lives:
        lives.sort()
        result["median_life_sec"] = lives[len(lives) // 2]
        result["instant_death_pct"] = round(
            result["instant_deaths"] / len(lives) * 100.0, 1)
    for sym in result["by_symbol"].values():
        sym["net"] = round(sym["net"], 2)

    result["findings"] = _findings(result)
    return result


def plural(n: int, one: str, few: str, many: str) -> str:
    """«1 сделка», «3 сделки», «5 сделок». Текст читает человек, а не машина:
    «3 сделок» в отчёте выглядит как ошибка и мешает доверять остальному."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return few
    return many


def _trades_word(n: int) -> str:
    return plural(n, "сделка", "сделки", "сделок")


def _findings(a: dict) -> list:
    """Короткие выводы обычными словами. Каждый — с указанием, что крутить."""
    out = []
    if a["trades"] < 10:
        out.append(f"Сделок пока мало ({a['trades']}). Любые выводы по такой "
                   f"выборке случайны — нужно хотя бы 30-50 сделок.")

    if a["instant_death_pct"] >= 30 and a["instant_deaths"] >= 3:
        out.append(
            f"{a['instant_deaths']} {_trades_word(a['instant_deaths'])} из "
            f"{a['trades']} закрылись быстрее "
            f"минуты ({a['instant_death_pct']}%). Это почти всегда стоп, "
            f"поставленный внутрь рыночного шума: цена не пошла никуда, её "
            f"мотнуло на спреде и выбило. Смотрите MIN_SL_SPREAD_MULTIPLE и "
            f"MIN_SL_ATR_FRACTION — они задают, насколько стоп обязан быть "
            f"дальше спреда и размаха свечи.")

    if a["payoff"] and a["payoff"] < 1.0 and a["losses"] >= 3:
        out.append(
            f"Средний плюс ({a['avg_win']}) меньше среднего минуса "
            f"({abs(a['avg_loss'])}). Чтобы такая система была в плюсе, нужно "
            f"выигрывать больше {round(100 / (1 + a['payoff']), 1)}% сделок. "
            f"Сейчас винрейт {a['win_rate']}%. Проверьте TP_TIGHTEN_MIN_R: "
            f"цель не должна становиться меньше собственного риска сделки.")

    if a["costs"] and abs(a["costs"]) >= abs(a["net"]) and a["net"] < 0:
        out.append(
            f"Комиссии и свопы съели {abs(a['costs'])} — это больше, чем весь "
            f"результат ({a['net']}). При таком размере сделки издержки решают "
            f"исход, а не сама торговля.")

    bad = sorted((s for s in a["by_symbol"].items() if s[1]["net"] < 0),
                 key=lambda kv: kv[1]["net"])
    if bad and len(a["by_symbol"]) > 1:
        name, data = bad[0]
        out.append(
            f"Больше всего минуса даёт {name}: {data['net']} за "
            f"{data['trades']} {_trades_word(data['trades'])}. "
            f"Имеет смысл временно снять эту пару "
            f"на вкладке «Символы» и посмотреть на остальные отдельно.")

    if not out:
        out.append("Явных перекосов в этих сделках не видно.")
    return out


# =====================================================================
# ФАЙЛЫ ЖУРНАЛА
# =====================================================================
HISTORY_COLUMNS = [
    ("open_time", "Открыта"),
    ("time", "Закрыта"),
    ("duration_sec", "Прожила, сек"),
    ("symbol", "Пара"),
    ("type", "Направление"),
    ("volume", "Лот"),
    ("open_price", "Цена входа"),
    ("price", "Цена выхода"),
    ("profit", "Результат"),
    ("commission", "Комиссия"),
    ("swap", "Своп"),
    ("is_bot", "Чья"),
    ("ticket", "Тикет"),
    ("comment", "Комментарий"),
]


def history_csv(deals: list) -> str:
    """История закрытых сделок из MetaTrader в виде CSV. Разделитель ';' —
    такой же, как у trades_log.csv, чтобы Excel открывал оба одинаково."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow([title for _, title in HISTORY_COLUMNS])
    for d in deals or []:
        row = []
        for key, _ in HISTORY_COLUMNS:
            value = d.get(key, "")
            if key == "is_bot":
                value = "бот" if value else "вручную"
            elif value is None:
                value = ""
            row.append(value)
        writer.writerow(row)
    return buffer.getvalue()


def summary_markdown(account: str, broker: str, stats: dict, a: dict,
                     generated: str) -> str:
    """Разбор для чтения глазами. Открывается прямо на сайте GitHub."""
    lines = [
        f"# Журнал сделок — счёт {account}",
        "",
        f"Брокер: {broker or 'не указан'}  ",
        f"Обновлено: {generated}",
        "",
        "## Коротко",
        "",
        "| Показатель | Значение |",
        "|---|---|",
        f"| Сделок бота | {a['trades']} |",
        f"| Из них в плюс | {a['wins']} |",
        f"| Из них в минус | {a['losses']} |",
        f"| Винрейт | {a['win_rate']}% |",
        f"| Итог | {a['net']} |",
        f"| Средний плюс | {a['avg_win']} |",
        f"| Средний минус | {a['avg_loss']} |",
        f"| Отношение плюс/минус | {a['payoff'] or '—'} |",
        f"| Комиссии и свопы | {a['costs']} |",
    ]
    if a["median_life_sec"] is not None:
        lines.append(f"| Обычное время жизни сделки | {a['median_life_sec']} сек |")
        lines.append(f"| Закрылись быстрее минуты | {a['instant_deaths']} "
                     f"({a['instant_death_pct']}%) |")
    lines += ["", "## Что видно по этим сделкам", ""]
    lines += [f"- {text}" for text in a["findings"]]

    if a["by_symbol"]:
        lines += ["", "## По парам", "",
                  "| Пара | Сделок | Итог | В плюс |", "|---|---|---|---|"]
        for name, data in sorted(a["by_symbol"].items(),
                                 key=lambda kv: kv[1]["net"]):
            lines.append(f"| {name} | {data['trades']} | {data['net']} | "
                         f"{data['wins']} |")

    if stats:
        lines += [
            "", "## Для сверки: то же самое из MetaTrader",
            "",
            f"За последние {stats.get('days', '-')} дней, по ВСЕМ сделкам счёта "
            f"(включая открытые вручную): сделок {stats.get('total_trades', 0)}, "
            f"винрейт {stats.get('win_rate', 0)}%, профит-фактор "
            f"{stats.get('profit_factor', 0)}, валовая прибыль "
            f"{stats.get('gross_profit', 0)}, валовый убыток "
            f"{stats.get('gross_loss', 0)}.",
        ]

    lines += [
        "", "---", "",
        "Файл собран программой автоматически. Здесь нет паролей, ключей и "
        "токенов — только сделки.",
    ]
    return "\n".join(lines) + "\n"


def local_trades_csv() -> str:
    """Собственный журнал бота, как есть. Пусто — если файла ещё нет."""
    path = getattr(cfg, "LOG_CSV_PATH", "trades_log.csv")
    if not os.path.isabs(path):
        path = os.path.join(app_dir(), path)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError as e:
        log.warning("Не удалось прочитать %s: %s", path, e)
        return ""


# =====================================================================
# ВЫГРУЗКА
# =====================================================================
_last_upload = {"ts": 0.0}


def last_upload_ts() -> float:
    """Когда журнал уходил в облако в последний раз (секунды epoch, 0 = никогда)."""
    return _last_upload["ts"]


def upload(snapshot: dict, now_text: str = "") -> dict:
    """Выложить журнал в облако.

    snapshot — снимок с дашборда (dashboard_state.get_snapshot()).
    Возвращает {"ok", "error", "files", "revision", "analysis"}."""
    result = {"ok": False, "error": "", "files": [], "revision": "", "analysis": {}}

    ok, reason = ready()
    if not ok:
        result["error"] = reason
        return result

    snapshot = snapshot or {}
    deals = snapshot.get("mt5_history", []) or []
    stats = snapshot.get("mt5_history_stats", {}) or {}
    account = account_label((snapshot.get("account") or {}).get("login", ""))
    broker = str((snapshot.get("account") or {}).get("server", ""))
    generated = now_text or time.strftime("%d.%m.%Y %H:%M:%S")

    analysis = analyze(deals)
    result["analysis"] = analysis

    base = f"{folder()}/{account}"
    payloads = [
        (f"{base}_history.csv", history_csv(deals)),
        (f"{base}_summary.md", summary_markdown(account, broker, stats,
                                                analysis, generated)),
    ]
    own = local_trades_csv()
    if own:
        payloads.append((f"{base}_trades.csv", own))

    message = (f"Журнал сделок {account}: {analysis['trades']} сделок, "
               f"итог {analysis['net']} ({generated})")
    try:
        for path, text in payloads:
            revision = put_file(path, text, message)
            result["files"].append(path)
            result["revision"] = revision or result["revision"]
    except Exception as e:  # noqa: BLE001
        result["error"] = explain_error(e)
        log.warning("Журнал в облако не ушёл: %s", result["error"])
        return result

    _last_upload["ts"] = time.time()
    result["ok"] = True
    return result


def upload_if_due(snapshot: dict) -> dict:
    """Вызывается из фонового цикла: выгружает не чаще, чем раз в
    JOURNAL_UPLOAD_MINUTES. Возвращает None, если время ещё не пришло."""
    if not enabled():
        return None
    if time.time() - _last_upload["ts"] < upload_interval_seconds():
        return None
    # Отметку времени ставим ДО отправки: иначе при недоступном GitHub цикл
    # долбился бы в сеть каждую секунду.
    _last_upload["ts"] = time.time()
    return upload(snapshot)


def web_url() -> str:
    """Ссылка, по которой журнал открывается в браузере."""
    if not repo():
        return ""
    return f"https://github.com/{repo()}/tree/{branch()}/{folder()}"
