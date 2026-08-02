"""Хранение торговых счетов: список, пароли, настройки риска.

Файл accounts.json лежит рядом с программой. Пароли в нём зашифрованы
(см. secrets.py) и в открытом виде не хранятся никогда.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import secrets

BASE_DIR = Path(__file__).resolve().parent.parent
ACCOUNTS_FILE = BASE_DIR / "accounts.json"


@dataclass
class Account:
    """Один торговый счёт."""

    name: str = ""                  # понятное имя, например "Демо Exness"
    login: int = 0                  # номер счёта
    server: str = ""                # торговый сервер брокера
    password: str = ""              # ТОЛЬКО в памяти, на диск идёт зашифрованным
    terminal_path: str = ""         # путь к terminal64.exe этой копии MT5
    enabled: bool = True            # участвует ли счёт в работе
    symbols: list[str] = field(default_factory=lambda: ["EURUSD", "XAUUSD"])

    # Ограничения риска — свои у каждого счёта
    risk_percent: float = 0.5       # риск на сделку, % от equity
    max_positions: int = 3          # максимум открытых позиций
    daily_loss_percent: float = 3.0 # дневной лимит убытка, %
    poll_interval_ms: int = 100     # как часто опрашивать терминал

    def display(self) -> str:
        title = self.name or f"Счёт {self.login}"
        return f"{title} · {self.login} · {self.server}"

    def validate(self) -> list[str]:
        """Список проблем, мешающих запустить счёт (пустой = всё в порядке)."""
        problems = []
        if self.login <= 0:
            problems.append("не указан номер счёта")
        if not self.server:
            problems.append("не указан сервер брокера")
        if not self.password:
            problems.append("не указан пароль")
        if not self.symbols:
            problems.append("не выбран ни один инструмент")
        if self.risk_percent <= 0 or self.risk_percent > 10:
            problems.append("риск на сделку должен быть от 0 до 10%")
        if self.max_positions < 1:
            problems.append("максимум позиций должен быть не меньше 1")
        if self.poll_interval_ms < MIN_POLL_MS:
            problems.append(f"интервал опроса не может быть меньше {MIN_POLL_MS} мс")
        return problems


# Ниже этого значения опрос бессмыслен: один запрос к MT5 сам занимает
# около 0.1-1 мс, и более частый цикл только займёт процессор впустую.
MIN_POLL_MS = 10


class AccountStore:
    """Загрузка и сохранение списка счетов."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else ACCOUNTS_FILE
        self.accounts: list[Account] = []

    def load(self) -> list[Account]:
        self.accounts = []
        if not self.path.exists():
            return self.accounts
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return self.accounts

        for item in data.get("accounts", []):
            account = Account()
            for key, value in item.items():
                if key == "password_encrypted":
                    try:
                        account.password = secrets.decrypt(value)
                    except OSError:
                        # Файл с другого компьютера — пароль недоступен,
                        # счёт остаётся в списке, но потребует повторного ввода
                        account.password = ""
                elif hasattr(account, key):
                    setattr(account, key, value)
            self.accounts.append(account)
        return self.accounts

    def save(self) -> None:
        payload = {"version": 1, "accounts": []}
        for account in self.accounts:
            item = asdict(account)
            plain = item.pop("password", "")
            item["password_encrypted"] = secrets.encrypt(plain)
            payload["accounts"].append(item)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Пишем через временный файл: сбой на середине не испортит список счетов
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, account: Account) -> None:
        self.accounts.append(account)
        self.save()

    def remove(self, login: int) -> bool:
        before = len(self.accounts)
        self.accounts = [a for a in self.accounts if a.login != login]
        if len(self.accounts) != before:
            self.save()
            return True
        return False

    def find(self, login: int) -> Account | None:
        for account in self.accounts:
            if account.login == login:
                return account
        return None

    def enabled_accounts(self) -> list[Account]:
        return [a for a in self.accounts if a.enabled and not a.validate()]
