"""Вкладка «Счета»: несколько торговых счетов MT5 в одном окне.

Вынесена отдельным модулем, чтобы desktop_app.py не разрастался: там нужно
добавить всего пару строк (см. конец этого файла — пример подключения).

Интерфейс НИКОГДА не обращается к MT5 напрямую — только к AccountSupervisor,
который держит счета в отдельных процессах. Поэтому окно не подвисает, даже
если брокер отвечает медленно или терминал завис.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import config as cfg
import control
from account_supervisor import AccountSupervisor
from accounts import MIN_POLL_MS, Account, AccountStore, migrate_from_config

# Оформление в тон остальному окну программы
BG = "#1b1b1b"
BG_CARD = "#232323"
FG = "#eeeeee"
FG_MUTED = "#9a9a9a"
FG_DIM = "#6a6a6a"
ACCENT = "#4c8dff"
PROFIT = "#3fb950"
LOSS = "#f0574a"
WARNING = "#d9a441"

REFRESH_MS = 500  # как часто перерисовывать вкладку

STATUS_COLORS = {
    "подключён": PROFIT,
    "запускается": WARNING,
    "остановлен": FG_DIM,
    "не запущен": FG_DIM,
    "ошибка": LOSS,
    "ошибка входа": LOSS,
    "нет связи": LOSS,
    "сбой опроса": LOSS,
    "аварийная остановка": LOSS,
}


def money(value: float, digits: int = 2) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.{digits}f}".replace(",", " ")


def money_color(value: float) -> str:
    if value > 0:
        return PROFIT
    if value < 0:
        return LOSS
    return FG_MUTED


class AccountDialog(tk.Toplevel):
    """Окно добавления и правки счёта."""

    FIELDS = [
        ("name", "Название", "например: Демо Exness"),
        ("login", "Номер счёта", "12345678"),
        ("password", "Пароль от счёта", ""),
        ("server", "Сервер брокера", "например: Exness-MT5Trial"),
        ("terminal_path", "Свой terminal64.exe", "пусто = работать по очереди с другими"),
        ("symbols", "Инструменты через запятую", "пусто = взять из настроек программы"),
        ("risk_percent", "Риск на сделку, %", "0.5"),
        ("max_positions", "Макс. позиций", "3"),
        ("daily_loss_percent", "Дневной лимит убытка, %", "3.0"),
        ("poll_interval_ms", "Интервал опроса, мс", "100"),
    ]

    def __init__(self, master, account: Account | None = None):
        super().__init__(master)
        self.result: Account | None = None
        self.account = account or Account()

        self.title("Изменить счёт" if account else "Добавить счёт")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        tk.Label(self, text="Данные торгового счёта", bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 13)).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(18, 4))
        tk.Label(self, text="Пароль сохранится зашифрованным (тот же механизм, "
                            "что и у остальных секретов программы).",
                 bg=BG, fg=FG_MUTED, font=("Segoe UI", 8), wraplength=430,
                 justify="left").grid(row=1, column=0, columnspan=2, sticky="w",
                                      padx=18, pady=(0, 10))

        self.entries: dict[str, tk.Entry] = {}
        self.hints: dict[str, str] = {}
        for i, (key, label, hint) in enumerate(self.FIELDS, start=2):
            tk.Label(self, text=label, bg=BG, fg=FG_MUTED, font=("Segoe UI", 9),
                     anchor="w").grid(row=i, column=0, sticky="w", padx=(18, 8), pady=3)
            entry = tk.Entry(self, bg=BG_CARD, fg=FG, insertbackground=FG,
                             relief="flat", font=("Segoe UI", 10), width=36,
                             show="*" if key == "password" else "")
            entry.grid(row=i, column=1, sticky="ew", padx=(0, 18), pady=3, ipady=3)
            self.entries[key] = entry
            self.hints[key] = hint

            value = getattr(self.account, key, "")
            if key == "symbols":
                value = ", ".join(self.account.symbols)
            if value not in ("", 0):
                entry.insert(0, str(value))

        row = len(self.FIELDS) + 2
        tk.Label(self, text="Пусто в поле «Свой terminal64.exe» — счёт будет "
                            "опрашиваться по очереди с другими такими же: один "
                            "терминал MT5 держит только один счёт одновременно.",
                 bg=BG, fg=FG_DIM, font=("Segoe UI", 8), wraplength=430,
                 justify="left").grid(row=row, column=0, columnspan=2,
                                      sticky="w", padx=18, pady=(8, 0))

        self.error = tk.Label(self, text="", bg=BG, fg=LOSS, font=("Segoe UI", 9),
                              wraplength=430, justify="left")
        self.error.grid(row=row + 1, column=0, columnspan=2, sticky="w",
                        padx=18, pady=(6, 0))

        buttons = tk.Frame(self, bg=BG)
        buttons.grid(row=row + 2, column=0, columnspan=2, sticky="e", padx=18, pady=16)
        tk.Button(buttons, text="Отмена", command=self.destroy, bg=BG_CARD, fg=FG,
                  relief="flat", font=("Segoe UI", 10), padx=14, pady=5,
                  activebackground="#2e2e2e", activeforeground=FG).pack(side="left", padx=6)
        tk.Button(buttons, text="Сохранить", command=self._save, bg=ACCENT, fg="white",
                  relief="flat", font=("Segoe UI Semibold", 10), padx=18, pady=5,
                  activebackground="#3d7ae8", activeforeground="white").pack(side="left")

        self.columnconfigure(1, weight=1)
        self.entries["name"].focus_set()

    def _save(self):
        acc = Account()
        try:
            acc.name = self.entries["name"].get().strip()
            acc.login = int(self.entries["login"].get().strip() or 0)
            acc.password = self.entries["password"].get()
            acc.server = self.entries["server"].get().strip()
            acc.terminal_path = self.entries["terminal_path"].get().strip()
            acc.symbols = [s.strip() for s in self.entries["symbols"].get().split(",") if s.strip()]
            acc.risk_percent = float(self.entries["risk_percent"].get().strip() or 0.5)
            acc.max_positions = int(self.entries["max_positions"].get().strip() or 3)
            acc.daily_loss_percent = float(self.entries["daily_loss_percent"].get().strip() or 3.0)
            acc.poll_interval_ms = int(self.entries["poll_interval_ms"].get().strip() or 100)
        except ValueError:
            self.error.configure(text="Проверьте числовые поля — там должны быть только цифры")
            return

        problems = acc.validate()
        if problems:
            self.error.configure(text="Не хватает данных: " + ", ".join(problems))
            return

        acc.enabled = self.account.enabled
        acc.magic = self.account.magic
        self.result = acc
        self.destroy()


class AccountsTab:
    """Содержимое вкладки «Счета»."""

    def __init__(self, parent: tk.Widget, root: tk.Misc):
        self.root = root
        self.store = AccountStore()
        self.supervisor = AccountSupervisor()
        self.selected_login: int | None = None

        self._load_accounts()
        self._build(parent)
        self._refresh_list()
        self.root.after(REFRESH_MS, self._tick)

    # ---------- данные ----------
    def _credentials(self) -> tuple[str, str]:
        """Пароль входа и соль — ими шифруются пароли счетов."""
        return control.get_session_password() or "", getattr(cfg, "SECURITY_SALT", "") or ""

    def _load_accounts(self):
        password, salt = self._credentials()
        self.store.load(password, salt)
        # Первый запуск многосчётной версии: переносим счёт из config.py,
        # чтобы уже введённые настройки брокера не потерялись
        if migrate_from_config(cfg, self.store, password, salt):
            self.store.load(password, salt)

    def _save_accounts(self):
        password, salt = self._credentials()
        self.store.save(password, salt)

    # ---------- построение ----------
    def _build(self, parent):
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="both", expand=True)

        # --- верхняя строка: сводка и аварийная кнопка ---
        top = tk.Frame(outer, bg=BG)
        top.pack(fill="x", padx=10, pady=(10, 6))

        self.summary = tk.Label(top, text="Счетов нет", bg=BG, fg=FG_MUTED,
                                font=("Segoe UI", 9), anchor="w")
        self.summary.pack(side="left")

        tk.Button(top, text="Закрыть всё на всех счетах", command=self._panic,
                  bg=LOSS, fg="white", relief="flat", font=("Segoe UI Semibold", 9),
                  padx=14, pady=5, activebackground="#c94438",
                  activeforeground="white").pack(side="right")

        # --- тело: слева счета, справа подробности ---
        body = tk.Frame(outer, bg=BG)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        left = tk.Frame(body, bg=BG, width=360)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)

        bar = tk.Frame(left, bg=BG)
        bar.pack(fill="x", pady=(0, 4))
        tk.Label(bar, text="СЧЕТА", bg=BG, fg=FG_DIM,
                 font=("Segoe UI", 8)).pack(side="left")
        tk.Button(bar, text="+ Добавить", command=self._add, bg=BG_CARD, fg=FG,
                  relief="flat", font=("Segoe UI", 8), padx=8, pady=3,
                  activebackground="#2e2e2e", activeforeground=FG).pack(side="right")

        # show="headings" без колонки #0: у неё есть отступ под значок дерева,
        # из-за которого текст в узкой панели обрезался
        self.tree = ttk.Treeview(left, columns=("name", "state", "profit"),
                                 show="headings", selectmode="browse", height=14)
        self.tree.heading("name", text="Счёт")
        self.tree.heading("state", text="Состояние")
        self.tree.heading("profit", text="Результат")
        self.tree.column("name", width=160, anchor="w", stretch=True)
        self.tree.column("state", width=110, anchor="center", stretch=False)
        self.tree.column("profit", width=85, anchor="e", stretch=False)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.tag_configure("profit", foreground=PROFIT)
        self.tree.tag_configure("loss", foreground=LOSS)
        self.tree.tag_configure("off", foreground=FG_DIM)

        manage = tk.Frame(left, bg=BG)
        manage.pack(fill="x", pady=(6, 0))
        for text, cmd in (("Изменить", self._edit), ("Удалить", self._delete)):
            tk.Button(manage, text=text, command=cmd, bg=BG_CARD, fg=FG_MUTED,
                      relief="flat", font=("Segoe UI", 8), padx=8, pady=4,
                      activebackground="#2e2e2e", activeforeground=FG).pack(side="left", padx=(0, 5))
        self.btn_enable = tk.Button(manage, text="Выключить", command=self._toggle,
                                    bg=BG_CARD, fg=FG_MUTED, relief="flat",
                                    font=("Segoe UI", 8), padx=8, pady=4,
                                    activebackground="#2e2e2e", activeforeground=FG)
        self.btn_enable.pack(side="left")

        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        cards = tk.Frame(right, bg=BG)
        cards.pack(fill="x", pady=(0, 8))
        self.card_equity = self._card(cards, "СРЕДСТВА")
        self.card_profit = self._card(cards, "РЕЗУЛЬТАТ")
        self.card_day = self._card(cards, "ЗА ДЕНЬ")
        self.card_pos = self._card(cards, "ПОЗИЦИЙ")

        self.status = tk.Label(right, text="Счёт не выбран", bg=BG, fg=FG_MUTED,
                               font=("Segoe UI", 9), anchor="w", wraplength=700,
                               justify="left")
        self.status.pack(fill="x", pady=(0, 6))

        actions = tk.Frame(right, bg=BG)
        actions.pack(fill="x", pady=(0, 8))
        self.btn_start = self._action(actions, "Запустить", self._start, ACCENT)
        self.btn_stop = self._action(actions, "Остановить", self._stop, BG_CARD)
        tk.Frame(actions, bg="#333", width=1).pack(side="left", fill="y", padx=6, pady=2)
        self._action(actions, "Запустить все", self._start_all, BG_CARD)
        tk.Frame(actions, bg="#333", width=1).pack(side="left", fill="y", padx=6, pady=2)
        self._action(actions, "Закрыть прибыльные", self._close_profit, BG_CARD)
        self._action(actions, "Закрыть убыточные", self._close_loss, BG_CARD)
        self._action(actions, "Закрыть все", self._close_all, BG_CARD)

        tk.Label(right, text="ОТКРЫТЫЕ ПОЗИЦИИ", bg=BG, fg=FG_DIM,
                 font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(4, 4))

        columns = ("symbol", "type", "volume", "open", "current", "sl", "tp", "profit")
        titles = ("Инструмент", "Тип", "Объём", "Вход", "Сейчас", "SL", "TP", "Результат")
        self.positions = ttk.Treeview(right, columns=columns, show="headings",
                                      selectmode="browse")
        for col, title in zip(columns, titles):
            self.positions.heading(col, text=title)
            self.positions.column(col, anchor="center", width=95)
        self.positions.pack(fill="both", expand=True)
        self.positions.tag_configure("profit", foreground=PROFIT)
        self.positions.tag_configure("loss", foreground=LOSS)
        self.positions.bind("<Double-1>", self._close_one)

        tk.Label(right, text="Двойной щелчок по строке закрывает позицию",
                 bg=BG, fg=FG_DIM, font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(4, 0))

    def _card(self, master, caption):
        card = tk.Frame(master, bg=BG_CARD)
        card.pack(side="left", fill="both", expand=True, padx=(0, 6))
        tk.Label(card, text=caption, bg=BG_CARD, fg=FG_DIM,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=10, pady=(8, 0))
        value = tk.Label(card, text="—", bg=BG_CARD, fg=FG,
                         font=("Segoe UI Light", 20))
        value.pack(anchor="w", padx=10, pady=(0, 8))
        return value

    def _action(self, master, text, command, bg):
        fg = "white" if bg == ACCENT else FG
        btn = tk.Button(master, text=text, command=command, bg=bg, fg=fg, relief="flat",
                        font=("Segoe UI", 9), padx=10, pady=5,
                        activebackground="#3d7ae8" if bg == ACCENT else "#2e2e2e",
                        activeforeground=fg)
        btn.pack(side="left", padx=(0, 5))
        return btn

    # ---------- список счетов ----------
    def _refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        for account in self.store.accounts:
            state = self.supervisor.state(account.login)
            tags = []
            if not account.enabled:
                tags.append("off")
            elif state.connected:
                tags.append("profit" if state.profit > 0 else "loss" if state.profit < 0 else "")
            self.tree.insert(
                "", "end", iid=str(account.login),
                values=(account.name or f"Счёт {account.login}", state.status,
                        money(state.profit) if state.connected else "—"),
                tags=tuple(t for t in tags if t))

        if self.selected_login is None and self.store.accounts:
            self.selected_login = self.store.accounts[0].login
        if self.selected_login is not None:
            iid = str(self.selected_login)
            if iid in self.tree.get_children():
                self.tree.selection_set(iid)

    def _on_select(self, _event=None):
        selection = self.tree.selection()
        if selection:
            self.selected_login = int(selection[0])
            self._refresh_details()

    def _selected(self) -> Account | None:
        if self.selected_login is None:
            return None
        return self.store.find(self.selected_login)

    # ---------- действия со счётом ----------
    def _add(self):
        dialog = AccountDialog(self.root)
        self.root.wait_window(dialog)
        if dialog.result is None:
            return
        if self.store.find(dialog.result.login):
            messagebox.showerror("Счёт уже есть",
                                 f"Счёт {dialog.result.login} уже в списке.")
            return
        password, salt = self._credentials()
        self.store.add(dialog.result, password, salt)
        self.selected_login = dialog.result.login
        self._refresh_list()

    def _edit(self):
        account = self._selected()
        if account is None:
            return
        if self.supervisor.is_running(account.login):
            messagebox.showwarning("Счёт запущен", "Сначала остановите счёт.")
            return
        dialog = AccountDialog(self.root, account)
        self.root.wait_window(dialog)
        if dialog.result is None:
            return
        password, salt = self._credentials()
        self.store.replace(account.login, dialog.result, password, salt)
        self.selected_login = dialog.result.login
        self._refresh_list()

    def _delete(self):
        account = self._selected()
        if account is None:
            return
        if not messagebox.askyesno("Удалить счёт",
                                   f"Удалить {account.display()} из списка?\n\n"
                                   "Открытые позиции на нём НЕ закрываются."):
            return
        self.supervisor.stop(account.login)
        password, salt = self._credentials()
        self.store.remove(account.login, password, salt)
        self.selected_login = None
        self._refresh_list()

    def _toggle(self):
        account = self._selected()
        if account is None:
            return
        if self.supervisor.is_running(account.login):
            messagebox.showwarning("Счёт запущен", "Сначала остановите счёт.")
            return
        account.enabled = not account.enabled
        self._save_accounts()
        self._refresh_list()

    # ---------- запуск и остановка ----------
    def _start(self):
        account = self._selected()
        if account is None:
            return
        started, messages = self.supervisor.start([account])
        if started == 0 and messages:
            messagebox.showerror("Не удалось запустить", "\n".join(messages))
        elif messages:
            messagebox.showinfo("Запущено", "\n".join(messages))

    def _start_all(self):
        ready = [a for a in self.store.ready_accounts()
                 if not self.supervisor.is_running(a.login)]
        if not ready:
            messagebox.showinfo("Нечего запускать",
                                "Все настроенные счета уже работают "
                                "(или ни один не настроен полностью).")
            return
        started, messages = self.supervisor.start(ready)
        text = f"Запущено счетов: {started}"
        if messages:
            text += "\n\n" + "\n".join(messages)
        messagebox.showinfo("Запуск", text)

    def _stop(self):
        if self.selected_login is not None:
            self.supervisor.stop(self.selected_login)

    # ---------- закрытие позиций ----------
    def _close_profit(self):
        if self.selected_login is not None:
            self.supervisor.close_profitable(self.selected_login)

    def _close_loss(self):
        if self.selected_login is not None:
            self.supervisor.close_losing(self.selected_login)

    def _close_all(self):
        if self.selected_login is not None:
            self.supervisor.close_all(self.selected_login)

    def _close_one(self, _event=None):
        item = self.positions.focus()
        if not item or self.selected_login is None:
            return
        for tag in self.positions.item(item, "tags"):
            if tag.startswith("ticket:"):
                self.supervisor.close_ticket(self.selected_login, int(tag.split(":")[1]))
                return

    def _panic(self):
        if not messagebox.askyesno(
            "Закрыть всё",
            "Закрыть ВСЕ позиции на ВСЕХ запущенных счетах?\n\n"
            "Это действие нельзя отменить."
        ):
            return
        sent = self.supervisor.close_all_everywhere()
        messagebox.showinfo("Команда отправлена",
                            f"Команда закрытия отправлена на счетов: {sent}")

    # ---------- обновление ----------
    def _tick(self):
        try:
            self.supervisor.pump()
            self._refresh_rows()
            self._refresh_details()
            self._refresh_summary()
        except Exception:  # noqa: BLE001
            pass  # сбой отрисовки не должен ронять всё окно программы
        self.root.after(REFRESH_MS, self._tick)

    def _refresh_rows(self):
        for account in self.store.accounts:
            iid = str(account.login)
            if iid not in self.tree.get_children():
                continue
            state = self.supervisor.state(account.login)
            status = state.status
            if self.supervisor.is_running(account.login) and self.supervisor.is_stale(account.login):
                status = "нет ответа"
            self.tree.item(iid, values=(account.name or f"Счёт {account.login}", status,
                                        money(state.profit) if state.connected else "—"))

    def _refresh_summary(self):
        totals = self.supervisor.totals()
        if not self.store.accounts:
            self.summary.configure(text="Счетов нет — нажмите «+ Добавить»", fg=FG_MUTED)
            return
        self.summary.configure(
            text=f"счетов: {len(self.store.accounts)} · запущено: {totals['running']} · "
                 f"подключено: {totals['connected']} · позиций: {totals['positions']} · "
                 f"общий результат: {money(totals['profit'])}",
            fg=money_color(totals["profit"]) if totals["running"] else FG_MUTED)

    def _refresh_details(self):
        account = self._selected()
        if account is None:
            self.status.configure(text="Счёт не выбран", fg=FG_MUTED)
            for card in (self.card_equity, self.card_profit, self.card_day, self.card_pos):
                card.configure(text="—", fg=FG_DIM)
            self.positions.delete(*self.positions.get_children())
            return

        state = self.supervisor.state(account.login)
        running = self.supervisor.is_running(account.login)

        status = state.status
        if running and self.supervisor.is_stale(account.login):
            status = "нет ответа от процесса"
        text = f"{account.display()} — {status}"
        if not account.enabled:
            text += " · счёт выключен"
        if not account.runs_in_parallel():
            text += " · общий терминал (опрос по очереди)"
        if state.error:
            text += f"\n{state.error}"
        if state.trading_blocked:
            text += f"\nТОРГОВЛЯ ОСТАНОВЛЕНА: {state.blocked_reason}"
        self.status.configure(text=text, fg=STATUS_COLORS.get(status, FG_MUTED))

        self.btn_start.configure(state="disabled" if running else "normal",
                                 bg=BG_CARD if running else ACCENT,
                                 fg=FG_DIM if running else "white")
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.btn_enable.configure(text="Выключить" if account.enabled else "Включить")

        if state.connected:
            self.card_equity.configure(text=f"{state.equity:,.2f}".replace(",", " "), fg=FG)
            self.card_profit.configure(text=money(state.profit), fg=money_color(state.profit))
            self.card_day.configure(text=f"{state.daily_pct:+.2f}%",
                                    fg=money_color(state.daily_pct))
            self.card_pos.configure(text=str(len(state.positions)), fg=FG)
        else:
            for card in (self.card_equity, self.card_profit, self.card_day, self.card_pos):
                card.configure(text="—", fg=FG_DIM)

        self._refresh_positions(state)

    def _refresh_positions(self, state):
        self.positions.delete(*self.positions.get_children())
        for p in state.positions:
            tag = "profit" if p["profit"] > 0 else "loss" if p["profit"] < 0 else ""

            def price(value):
                return f"{value:.5f}".rstrip("0").rstrip(".") if value else "—"

            self.positions.insert(
                "", "end",
                values=(p["symbol"], p["type"], f"{p['volume']:.2f}",
                        price(p["price_open"]), price(p["price_current"]),
                        price(p["sl"]), price(p["tp"]), money(p["profit"])),
                tags=(tag, f"ticket:{p['ticket']}"))

    # ---------- завершение ----------
    def shutdown(self):
        """Остановить все счета при закрытии программы."""
        self.supervisor.stop_all()


# ---------------------------------------------------------------------------
# Подключение в desktop_app.py — три строки:
#
#   from accounts_tab import AccountsTab              # рядом с прочими import
#   tab_accounts = ttk.Frame(self.notebook)           # рядом с другими вкладками
#   "Счета": tab_accounts,                            # в словарь self.tab_frames
#   self.accounts_tab = AccountsTab(tab_accounts, self.root)   # после остальных _build_tab_*
#
# И при закрытии окна: self.accounts_tab.shutdown()
# ---------------------------------------------------------------------------
