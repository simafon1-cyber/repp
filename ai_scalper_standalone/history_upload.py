"""history_upload.py — выгруженная история уезжает на GitHub сама.

=====================================================================
ЗАЧЕМ
=====================================================================
Владелец: «сделай, пусть сам выгружает на GitHub, и ты проверял их сам».

До сих пор круг был такой: нажать кнопку, найти папку, заархивировать,
прислать в чат. Четыре действия человека на каждое обновление данных — и
каждое место, где можно споткнуться (папку не нашли, архив не влез, файлы
уехали не туда). Теперь программа кладёт данные в репозиторий сама, а
проверяющий берёт их оттуда.

=====================================================================
ДВА РЕШЕНИЯ, КОТОРЫЕ НАДО ОБЪЯСНИТЬ
=====================================================================
1. ОТДЕЛЬНАЯ ВЕТКА. Данные уходят в ветку history-data, а не в рабочую.
   Котировки — это не исходный код: смешивать их с программой значит
   каждый раз тянуть десятки мегабайт всем, кто просто хочет посмотреть
   код. В отдельной ветке они никому не мешают и берутся по прямой ссылке.

2. СЖАТИЕ. Файлы уходят в .gz. Свечи — это столбцы чисел, они сжимаются
   раз в пять: 2.6 МБ превращаются в полмегабайта. Двадцать четыре файла
   без сжатия — это шестьдесят мегабайт на каждую выгрузку, и они
   останутся в репозитории навсегда.

ЧЕСТНО О ЦЕНЕ. Даже сжатые данные остаются в истории репозитория навсегда:
git не забывает ничего. Поэтому выгружать стоит тогда, когда данные
действительно обновились, а не по привычке.

=====================================================================
ЧТО НЕ УЙДЁТ ОТСЮДА НИКОГДА
=====================================================================
Только свечи и паспорт данных: цены, объёмы, спред, имя брокера, номер
счёта. Ни паролей, ни ключей, ни токенов, ни истории сделок здесь нет — эти
файлы просто не читаются этим модулем.

Токен берётся тот же, что у журнала сделок (JOURNAL_TOKEN): у него уже есть
право записи, и заводить второй секрет ради того же самого незачем.
"""

import base64
import gzip
import json
import logging
import os
import urllib.error
import urllib.request

import config as cfg
import cloud_journal as cj
import history_export

log = logging.getLogger("history_upload")

API = "https://api.github.com"

# Ветка для данных. Отдельная сознательно — см. пояснение выше.
BRANCH = "history-data"
FOLDER = "history"


def repo() -> str:
    """Репозиторий для данных: тот же, откуда программа берёт обновления.

    Отдельную настройку заводить незачем: репозиторий у проекта один, и
    лишний адрес — это лишнее место, где можно ошибиться."""
    свой = str(getattr(cfg, "JOURNAL_REPO", "") or "").strip().strip("/")
    return свой or str(getattr(cfg, "UPDATE_REPO", "") or "").strip().strip("/")


def token() -> str:
    """Токен С ПРАВОМ ЗАПИСИ. Тот же, что у журнала сделок."""
    return cj.token()


def ready() -> tuple:
    """Можно ли отправлять. (True, "") или (False, "почему нельзя")."""
    if not repo():
        return False, ("Не указан репозиторий. Вкладка «Система» -> "
                       "«Обновление из GitHub» -> поле «Репозиторий».")
    if not token():
        if cj.token_locked():
            return False, ("Токен записи зашифрован и ещё не расшифрован — "
                           "введите пароль программы.")
        return False, ("Нет токена с правом записи. Вкладка «Система» -> "
                       "«Журнал сделок в облаке» -> поле «Токен GitHub». "
                       "Нужны права Contents: Read and write.")
    return True, ""


def _request(method: str, url: str, payload: dict = None, timeout: int = 60):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "AI-Scalper-History",
        "Authorization": f"Bearer {token()}",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    return urllib.request.urlopen(req, timeout=timeout)


def default_branch() -> str:
    with _request("GET", f"{API}/repos/{repo()}") as r:
        return str(json.loads(r.read().decode("utf-8")).get("default_branch", "main"))


def ensure_branch() -> str:
    """Создать ветку данных, если её ещё нет. Возвращает, что произошло."""
    try:
        with _request("GET", f"{API}/repos/{repo()}/git/ref/heads/{BRANCH}"):
            return "была"
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    основная = default_branch()
    with _request("GET", f"{API}/repos/{repo()}/git/ref/heads/{основная}") as r:
        sha = json.loads(r.read().decode("utf-8"))["object"]["sha"]
    _request("POST", f"{API}/repos/{repo()}/git/refs",
             {"ref": f"refs/heads/{BRANCH}", "sha": sha}).close()
    log.info("Создана ветка %s для данных", BRANCH)
    return "создана"


def remote_sha(path: str):
    """sha файла в ветке данных или None. GitHub требует его при перезаписи."""
    url = f"{API}/repos/{repo()}/contents/{path}?ref={BRANCH}"
    try:
        with _request("GET", url) as r:
            return json.loads(r.read().decode("utf-8")).get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def put_bytes(path: str, raw: bytes, message: str) -> str:
    """Записать двоичный файл в ветку данных. Возвращает номер коммита."""
    payload = {
        "message": message,
        "content": base64.b64encode(raw).decode("ascii"),
        "branch": BRANCH,
    }
    sha = remote_sha(path)
    if sha:
        payload["sha"] = sha
    with _request("PUT", f"{API}/repos/{repo()}/contents/{path}", payload) as r:
        data = json.loads(r.read().decode("utf-8"))
    return str((data.get("commit") or {}).get("sha", ""))[:12]


def pack(path: str) -> bytes:
    """Сжать файл. Свечи — столбцы чисел, сжимаются примерно в пять раз."""
    with open(path, "rb") as f:
        return gzip.compress(f.read(), compresslevel=9)


def upload_all(symbols=None, timeframes=None, folder: str = "",
               progress=None) -> dict:
    """Отправить всё, что выгружено, в ветку данных.

    Возвращает {"ok", "sent", "skipped", "bytes", "errors", "url"}."""
    итог = {"ok": False, "sent": 0, "skipped": 0, "bytes": 0,
            "errors": [], "url": ""}
    можно, почему = ready()
    if not можно:
        итог["errors"].append(почему)
        return итог

    symbols = symbols or history_export.DEFAULT_SYMBOLS
    timeframes = timeframes or history_export.DEFAULT_TIMEFRAMES

    def сказать(текст):
        if progress:
            try:
                progress(текст)
            except Exception:  # noqa: BLE001
                pass

    try:
        сказать("Готовлю ветку для данных...")
        ensure_branch()
    except Exception as e:  # noqa: BLE001
        итог["errors"].append(f"Не удалось создать ветку {BRANCH}: "
                              f"{cj.explain_error(e)}")
        return итог

    файлы = []
    for тф in timeframes:
        for символ in symbols:
            csv_путь = history_export.raw_path(символ, тф, folder)
            if os.path.exists(csv_путь):
                файлы.append((csv_путь, f"{FOLDER}/{символ}_{тф}.csv.gz"))
            meta_путь = history_export.meta_path(символ, тф, folder)
            if os.path.exists(meta_путь):
                файлы.append((meta_путь, f"{FOLDER}/{символ}_{тф}.meta.json"))

    if not файлы:
        итог["errors"].append("Выгруженных файлов не найдено — сначала "
                              "нажмите «Выгрузить историю».")
        return итог

    for i, (откуда, куда) in enumerate(файлы, 1):
        сказать(f"[{i}/{len(файлы)}] отправляю {os.path.basename(куда)}...")
        try:
            # Паспорт данных маленький и его удобно читать глазами прямо на
            # GitHub — его не сжимаем. Свечи сжимаем всегда.
            данные = (pack(откуда) if куда.endswith(".gz")
                      else open(откуда, "rb").read())
            put_bytes(куда, данные, f"История: {os.path.basename(куда)}")
            итог["sent"] += 1
            итог["bytes"] += len(данные)
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось отправить %s: %s", куда, e)
            итог["errors"].append(f"{os.path.basename(куда)}: {cj.explain_error(e)}")
            итог["skipped"] += 1

    итог["ok"] = итог["sent"] > 0
    итог["url"] = f"https://github.com/{repo()}/tree/{BRANCH}/{FOLDER}"
    return итог


def describe(result: dict) -> str:
    """Что получилось — словами."""
    if not result.get("sent") and result.get("errors"):
        return "Не отправлено: " + "; ".join(result["errors"][:2])
    мб = result.get("bytes", 0) / 1024 / 1024
    строки = [f"Отправлено файлов: {result['sent']} ({мб:.1f} МБ сжатых)."]
    if result.get("skipped"):
        строки.append(f"Не удалось: {result['skipped']} — "
                      + "; ".join(result.get("errors", [])[:2]))
    if result.get("url"):
        строки.append(f"Смотреть: {result['url']}")
    return " ".join(строки)
