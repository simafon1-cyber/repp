"""Несколько торговых счетов MT5 в одной программе.

Раньше программа работала с одним счётом (MT5_LOGIN в config.py). Этот
модуль добавляет список счетов: у каждого свои логин, сервер, инструменты
и лимиты риска.

Пароли шифруются ТЕМ ЖЕ механизмом, что и остальные секреты программы —
secure_store (Fernet + PBKDF2, 200 000 итераций), ключ выводится из пароля
входа. На диске пароль счёта лежит только в виде "enc:...".

Старый одиночный счёт из config.py никуда не девается: если список счетов
пуст, программа работает по-прежнему (см. migrate_from_config).
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import secure_store

def app_dir() -> Path:
    """Папка, рядом с которой лежат ДАННЫЕ программы (accounts.json).

    КРИТИЧНО для собранного .exe. Раньше здесь было просто
    Path(__file__).resolve().parent — папка самого модуля. В onefile-сборке
    PyInstaller распаковывает модули во ВРЕМЕННУЮ папку (sys._MEIPASS) и
    УДАЛЯЕТ её при выходе из программы. Значит accounts.json записывался туда
    же и исчезал вместе с ней: добавленные счета пропадали при каждом
    перезапуске, и выглядело это как «программа удаляет мои счета».

    Рядом с .exe — то место, которое переживает перезапуск. Точно так же
    считают путь updater, cloud_journal и config_migrate."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = app_dir()
ACCOUNTS_FILE = BASE_DIR / "accounts.json"

# Ниже 10 мс опрос бессмыслен: один запрос к MT5 сам занимает 0.1-1 мс,
# а котировки приходят в лучшем случае несколько раз в секунду.
MIN_POLL_MS = 10
DEFAULT_POLL_MS = 100


@dataclass
class Account:
    """Один торговый счёт."""

    name: str = ""                 # понятное имя, например "Демо Exness"
    login: int = 0                 # номер счёта у брокера
    server: str = ""               # имя торгового сервера
    password: str = ""             # в памяти открытый, на диск идёт зашифрованным
    terminal_path: str = ""        # своя копия terminal64.exe (для параллельной работы)
    enabled: bool = True

    symbols: list[str] = field(default_factory=list)   # пусто = взять SYMBOLS из config
    magic: int = 0                 # 0 = взять MAGIC_NUMBER из config

    # Лимиты риска — свои у каждого счёта
    risk_percent: float = 0.5
    max_positions: int = 3
    # 0 = дневного порога убытка НЕТ. Раньше здесь стояло 3.0, и счёт сам
    # переставал торговать до завтра, поймав −3% за день. Это единственная
    # дневная остановка, которая ещё работала: общий USE_DAILY_LOSS_LIMIT
    # уже выключен, а этот порог живёт в accounts.json и глобальную галочку
    # не читает. Владелец программы просил остановки убрать — бот должен
    # отрабатывать всё торговое время, а убыток ограничивается размером
    # каждой сделки (стоп-лосс, риск на сделку, потолок риска), а не
    # молчанием до следующего дня.
    daily_loss_percent: float = 0.0
    poll_interval_ms: int = DEFAULT_POLL_MS

    def display(self) -> str:
        title = self.name or f"Счёт {self.login}"
        return f"{title} · {self.login} · {self.server}"

    def validate(self) -> list[str]:
        """Список причин, мешающих запустить счёт (пустой = всё в порядке)."""
        problems = []
        if self.login <= 0:
            problems.append("не указан номер счёта")
        if not self.server:
            problems.append("не указан сервер брокера")
        if not self.password:
            if self.password_locked():
                problems.append("пароль не расшифрован (войдите в программу с "
                                "правильным паролем входа)")
            else:
                problems.append("не указан пароль")
        if self.risk_percent <= 0 or self.risk_percent > 10:
            problems.append("риск на сделку должен быть от 0 до 10%")
        if self.max_positions < 1:
            problems.append("максимум позиций должен быть не меньше 1")
        if self.poll_interval_ms < MIN_POLL_MS:
            problems.append(f"интервал опроса не может быть меньше {MIN_POLL_MS} мс")
        return problems

    def password_locked(self) -> bool:
        """Пароль счёта на диске ЕСТЬ (зашифрован), но сейчас не расшифрован —
        текущий пароль входа (или его отсутствие: REQUIRE_LOGIN=False по
        умолчанию) не тот, которым счёт сохранялся. Не путать с «пароль не
        задан вовсе» — там и на диске пусто."""
        return bool(getattr(self, "_password_encrypted", "")) and not self.password

    def runs_in_parallel(self) -> bool:
        """True, если у счёта своя копия терминала.

        Один терминал MT5 может торговать только одним счётом одновременно —
        это ограничение самого MetaTrader. Счета со своей копией терминала
        работают параллельно; остальные программа опрашивает по очереди.
        """
        return bool(self.terminal_path)


class AccountStore:
    """Список счетов на диске. Пароли всегда зашифрованы."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else ACCOUNTS_FILE
        self.accounts: list[Account] = []
        # Служебные пометки в том же файле, которые пишет НЕ этот класс
        # (например, migrated_daily_loss_off от config_migrate). Их надо
        # сохранить при перезаписи: save() собирает файл заново, и без этого
        # отметка «миграция уже применялась» терялась бы после любого
        # действия на вкладке «Счета» — а значит миграция срабатывала бы
        # снова и снова, стирая то, что человек вписал осознанно.
        self.extra: dict = {}

    # ---------- чтение и запись ----------
    def load(self, password: str, salt: str) -> list[Account]:
        """password/salt — пароль входа в программу и SECURITY_SALT из config."""
        self.accounts = []
        self.extra = {}
        if not self.path.exists():
            return self.accounts
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Испорченный файл не должен ронять программу
            return self.accounts

        self.extra = {k: v for k, v in data.items()
                      if k not in ("version", "accounts")}

        for item in data.get("accounts", []):
            account = Account()
            for key, value in item.items():
                if key == "password_encrypted":
                    # Запоминаем зашифрованную строку КАК ЕСТЬ, независимо от
                    # того, удалось её расшифровать или нет — см. save().
                    account._password_encrypted = value or ""
                    if value:
                        try:
                            account.password = secure_store.decrypt_value(value, password, salt)
                        except Exception:  # noqa: BLE001
                            # Неверный пароль входа (например, программа
                            # запущена с REQUIRE_LOGIN=False и без "запомненного"
                            # пароля) или файл с другого компьютера: счёт
                            # остаётся в списке, потребует пароль входа заново.
                            # ГЛАВНОЕ: сам зашифрованный пароль счёта (выше)
                            # никуда не делся — только не расшифрован СЕЙЧАС.
                            account.password = ""
                    else:
                        account.password = ""
                elif hasattr(account, key):
                    setattr(account, key, value)
            self.accounts.append(account)
        return self.accounts

    def save(self, password: str, salt: str) -> None:
        payload = {"version": 1, "accounts": []}
        payload.update(self.extra)   # чужие пометки не теряются (см. __init__)
        for account in self.accounts:
            item = asdict(account)
            plain = item.pop("password", "")
            if plain:
                item["password_encrypted"] = secure_store.encrypt_value(plain, password, salt)
            else:
                # КРИТИЧНО. Пустой пароль в памяти НЕ обязательно означает
                # «пользователь его убрал» — так же выглядит счёт, чей пароль
                # просто не удалось расшифровать ТЕКУЩИМ паролем входа
                # (например, программа запущена без входа — REQUIRE_LOGIN=False
                # по умолчанию, — а счёт был сохранён при другом пароле).
                # Раньше в этом случае save() шифровал пустую строку и
                # НАВСЕГДА уничтожал настоящий пароль счёта одним нажатием
                # любой другой кнопки на вкладке «Счета» (переключить галочку,
                # добавить/удалить ДРУГОЙ счёт — save() перезаписывает файл
                # целиком). Теперь при пустом пароле сохраняется ТА ЖЕ
                # зашифрованная строка, что была прочитана при загрузке —
                # ничего не портится, пока пользователь не введёт настоящий
                # пароль входа и не сохранит счёт заново осознанно.
                item["password_encrypted"] = getattr(account, "_password_encrypted", "")
            payload["accounts"].append(item)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Пишем через временный файл: сбой на середине не испортит список счетов
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        # После сохранения зашифрованное представление в памяти должно
        # совпадать с тем, что только что легло на диск — иначе повторное
        # сохранение (например, следующим действием пользователя) снова
        # опиралось бы на устаревшую строку.
        for account, item in zip(self.accounts, payload["accounts"]):
            account._password_encrypted = item.get("password_encrypted", "")

    # ---------- операции со списком ----------
    def add(self, account: Account, password: str, salt: str) -> None:
        self.accounts.append(account)
        self.save(password, salt)

    def replace(self, login: int, account: Account, password: str, salt: str) -> None:
        self.accounts = [account if a.login == login else a for a in self.accounts]
        self.save(password, salt)

    def remove(self, login: int, password: str, salt: str) -> bool:
        before = len(self.accounts)
        self.accounts = [a for a in self.accounts if a.login != login]
        if len(self.accounts) == before:
            return False
        self.save(password, salt)
        return True

    def find(self, login: int) -> Account | None:
        for account in self.accounts:
            if account.login == login:
                return account
        return None

    def ready_accounts(self) -> list[Account]:
        """Счета, которые включены и полностью настроены."""
        return [a for a in self.accounts if a.enabled and not a.validate()]

    def parallel_accounts(self) -> list[Account]:
        return [a for a in self.ready_accounts() if a.runs_in_parallel()]

    def sequential_accounts(self) -> list[Account]:
        return [a for a in self.ready_accounts() if not a.runs_in_parallel()]


def migrate_from_config(cfg_module, store: AccountStore, password: str, salt: str) -> bool:
    """Переносит одиночный счёт из config.py в список счетов.

    Нужно один раз при переходе на многосчётную версию: настройки брокера,
    которые пользователь уже ввёл на вкладке "Брокер", не должны потеряться.
    Возвращает True, если счёт был добавлен.
    """
    if store.accounts:
        return False  # список уже заполнен, ничего не трогаем
    login = int(getattr(cfg_module, "MT5_LOGIN", 0) or 0)
    if login <= 0:
        return False

    account = Account(
        name="Основной счёт",
        login=login,
        server=getattr(cfg_module, "MT5_SERVER", "") or "",
        password=getattr(cfg_module, "MT5_PASSWORD", "") or "",
        terminal_path=getattr(cfg_module, "MT5_TERMINAL_PATH", "") or "",
        symbols=list(getattr(cfg_module, "SYMBOLS", []) or []),
        magic=int(getattr(cfg_module, "MAGIC_NUMBER", 0) or 0),
    )
    store.add(account, password, salt)
    return True


# ===========================================================================
# СОПОСТАВЛЕНИЕ ПАР С ИМЕНАМИ БРОКЕРА
#
# Одна и та же пара у разных брокеров называется по-разному: EURUSD,
# EURUSDs, EURUSD.a, EURUSDm, EURUSD_i. Поэтому список пар берётся у самого
# брокера (см. account_supervisor.refresh_symbols), а желаемые имена
# сопоставляются с ним здесь.
# ===========================================================================


def _normalize(name: str) -> str:
    """EURUSD.a -> EURUSDA, eur/usd -> EURUSD. Для сравнения имён."""
    return "".join(ch for ch in name.upper() if ch.isalnum())


def resolve_symbol(wanted: str, available: list) -> str | None:
    """Находит имя пары у брокера по желаемому имени.

    Порядок: точное совпадение, затем совпадение без учёта регистра и
    разделителей, затем имя брокера, начинающееся с желаемого (суффикс).
    Из нескольких подходящих берётся самое короткое — у него минимальный
    суффикс, то есть это базовая пара, а не производная.
    """
    if not wanted or not available:
        return None

    if wanted in available:
        return wanted

    target = _normalize(wanted)
    if not target:
        return None

    exact = [a for a in available if _normalize(a) == target]
    if exact:
        return min(exact, key=len)

    # EURUSD -> EURUSDs / EURUSD.a: имя брокера начинается с желаемого
    prefixed = [a for a in available if _normalize(a).startswith(target)]
    if prefixed:
        return min(prefixed, key=len)

    # ОБРАТНЫЙ случай: суффикс в ЖЕЛАЕМОМ имени, а у брокера его нет.
    # EURUSDs -> EURUSD. Так выглядит переезд на другого брокера: в
    # настройках остались имена прежнего (у SwitchMarkets все пары с "s"),
    # а новый брокер называет их без суффикса. Раньше не находилась НИ ОДНА
    # пара — бот молча не торговал ничем, и понять почему было нельзя.
    #
    # Отрезаем не больше трёх символов с конца и не короче шести: шесть —
    # это длина обычной валютной пары (EURUSD), укорачивать сильнее значило
    # бы сопоставлять пары наугад.
    for cut in range(1, 4):
        if len(target) - cut < 6:
            break
        trimmed = target[:-cut]
        same = [a for a in available if _normalize(a) == trimmed]
        if same:
            return min(same, key=len)

    return None


def resolve_symbols(wanted: list, available: list) -> tuple:
    """Сопоставляет список пар. Возвращает (соответствия, ненайденные)."""
    mapping = {}
    missing = []
    for name in wanted or []:
        found = resolve_symbol(name, available)
        if found:
            mapping[name] = found
        else:
            missing.append(name)
    return mapping, missing
