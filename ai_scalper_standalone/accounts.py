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
from dataclasses import asdict, dataclass, field
from pathlib import Path

import secure_store

BASE_DIR = Path(__file__).resolve().parent
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
    daily_loss_percent: float = 3.0
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
            problems.append("не указан пароль")
        if self.risk_percent <= 0 or self.risk_percent > 10:
            problems.append("риск на сделку должен быть от 0 до 10%")
        if self.max_positions < 1:
            problems.append("максимум позиций должен быть не меньше 1")
        if self.poll_interval_ms < MIN_POLL_MS:
            problems.append(f"интервал опроса не может быть меньше {MIN_POLL_MS} мс")
        return problems

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

    # ---------- чтение и запись ----------
    def load(self, password: str, salt: str) -> list[Account]:
        """password/salt — пароль входа в программу и SECURITY_SALT из config."""
        self.accounts = []
        if not self.path.exists():
            return self.accounts
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Испорченный файл не должен ронять программу
            return self.accounts

        for item in data.get("accounts", []):
            account = Account()
            for key, value in item.items():
                if key == "password_encrypted":
                    try:
                        account.password = secure_store.decrypt_value(value, password, salt)
                    except Exception:  # noqa: BLE001
                        # Неверный пароль входа или файл с другого компьютера:
                        # счёт остаётся в списке, но потребует ввести пароль заново
                        account.password = ""
                elif hasattr(account, key):
                    setattr(account, key, value)
            self.accounts.append(account)
        return self.accounts

    def save(self, password: str, salt: str) -> None:
        payload = {"version": 1, "accounts": []}
        for account in self.accounts:
            item = asdict(account)
            plain = item.pop("password", "")
            item["password_encrypted"] = secure_store.encrypt_value(plain, password, salt)
            payload["accounts"].append(item)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Пишем через временный файл: сбой на середине не испортит список счетов
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

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
