"""Главное окно программы.

Устроено так: слева список счетов, справа — сводка, позиции и кнопки.
Ничего лишнего на экране: видно состояние каждого счёта, общий результат
и всё, что нужно, чтобы не открывать MetaTrader.

Интерфейс НИКОГДА не обращается к MT5 напрямую — только к Supervisor,
поэтому окно не подвисает из-за медленного ответа брокера.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from core.accounts import MIN_POLL_MS, Account, AccountStore
from core.secrets import storage_status
from core.supervisor import Supervisor

from . import theme as T

UI_REFRESH_MS = 250  # как часто перерисовывать окно


class AccountRow(tk.Frame):
    """Строка счёта в левом списке."""

    def __init__(self, master, account: Account, on_select):
        super().__init__(master, bg=T.BG_CARD, highlightthickness=0)
        self.account = account
        self.on_select = on_select
        self.selected = False

        self.dot = tk.Label(self, text="●", bg=T.BG_CARD, fg=T.FG_DIM, font=T.FONT_BODY)
        self.dot.pack(side="left", padx=(T.PAD, T.PAD_S))

        box = tk.Frame(self, bg=T.BG_CARD)
        box.pack(side="left", fill="x", expand=True, pady=T.PAD_S)

        title_fg = T.FG if account.enabled else T.FG_DIM
        self.title = tk.Label(box, text=account.name or f"Счёт {account.login}",
                              bg=T.BG_CARD, fg=title_fg, font=T.FONT_HEAD, anchor="w")
        self.title.pack(fill="x")
        self.subtitle = tk.Label(box, text=f"{account.login} · {account.server}",
                                 bg=T.BG_CARD, fg=T.FG_MUTED, font=T.FONT_SMALL, anchor="w")
        self.subtitle.pack(fill="x")

        self.money = tk.Label(self, text="—", bg=T.BG_CARD, fg=T.FG_MUTED,
                              font=T.FONT_BODY)
        self.money.pack(side="right", padx=T.PAD)

        for widget in (self, self.dot, box, self.title, self.subtitle, self.money):
            widget.bind("<Button-1>", self._click)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    def _click(self, _event=None):
        self.on_select(self.account.login)

    def _paint(self, bg):
        for widget in (self, self.dot, self.money, self.title, self.subtitle):
            widget.configure(bg=bg)
        for child in self.winfo_children():
            if isinstance(child, tk.Frame):
                child.configure(bg=bg)

    def _enter(self, _e=None):
        if not self.selected:
            self._paint(T.BG_HOVER)

    def _leave(self, _e=None):
        if not self.selected:
            self._paint(T.BG_CARD)

    def set_selected(self, value: bool):
        self.selected = value
        self._paint(T.BG_HOVER if value else T.BG_CARD)

    def update_state(self, state):
        self.dot.configure(fg=T.status_color(state.status))
        if state.connected:
            self.money.configure(text=T.money(state.profit),
                                 fg=T.money_color(state.profit))
        else:
            self.money.configure(text="—", fg=T.FG_DIM)


class AccountDialog(tk.Toplevel):
    """Окно добавления и правки счёта."""

    FIELDS = [
        ("name", "Название", "Например: Демо Exness"),
        ("login", "Номер счёта", "12345678"),
        ("password", "Пароль", ""),
        ("server", "Сервер брокера", "Exness-MT5Trial"),
        ("terminal_path", "Путь к terminal64.exe", "оставьте пустым для терминала по умолчанию"),
        ("symbols", "Инструменты через запятую", "EURUSD, XAUUSD"),
        ("risk_percent", "Риск на сделку, %", "0.5"),
        ("max_positions", "Макс. позиций", "3"),
        ("daily_loss_percent", "Дневной лимит убытка, %", "3.0"),
        ("poll_interval_ms", "Интервал опроса, мс", "100"),
    ]

    def __init__(self, master, account: Account | None = None):
        super().__init__(master)
        self.result: Account | None = None
        self.account = account or Account()
        is_edit = account is not None

        self.title("Изменить счёт" if is_edit else "Добавить счёт")
        self.configure(bg=T.BG)
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        tk.Label(self, text="Данные счёта", bg=T.BG, fg=T.FG,
                 font=T.FONT_TITLE).grid(row=0, column=0, columnspan=2,
                                         sticky="w", padx=T.PAD_L, pady=(T.PAD_L, T.PAD_S))
        tk.Label(self, text=storage_status(), bg=T.BG, fg=T.FG_MUTED,
                 font=T.FONT_SMALL, wraplength=420, justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", padx=T.PAD_L, pady=(0, T.PAD))

        self.entries: dict[str, tk.Entry] = {}
        for i, (key, label, hint) in enumerate(self.FIELDS, start=2):
            tk.Label(self, text=label, bg=T.BG, fg=T.FG_MUTED,
                     font=T.FONT_SMALL, anchor="w").grid(
                row=i, column=0, sticky="w", padx=(T.PAD_L, T.PAD_S), pady=3)
            entry = tk.Entry(self, bg=T.BG_CARD, fg=T.FG, insertbackground=T.FG,
                             relief="flat", font=T.FONT_BODY, width=34,
                             show="•" if key == "password" else "")
            entry.grid(row=i, column=1, sticky="ew", padx=(0, T.PAD_L), pady=3, ipady=4)
            self.entries[key] = entry

            value = getattr(self.account, key, "")
            if key == "symbols":
                value = ", ".join(self.account.symbols)
            entry.insert(0, "" if value in (0, "") else str(value))
            if not entry.get() and hint:
                entry.insert(0, hint if key in ("server", "name") else "")

        self.error = tk.Label(self, text="", bg=T.BG, fg=T.LOSS,
                              font=T.FONT_SMALL, wraplength=420, justify="left")
        self.error.grid(row=99, column=0, columnspan=2, sticky="w",
                        padx=T.PAD_L, pady=(T.PAD_S, 0))

        buttons = tk.Frame(self, bg=T.BG)
        buttons.grid(row=100, column=0, columnspan=2, sticky="e",
                     padx=T.PAD_L, pady=T.PAD_L)
        tk.Button(buttons, text="Отмена", command=self.destroy, bg=T.BG_CARD, fg=T.FG,
                  relief="flat", font=T.FONT_BODY, padx=14, pady=6,
                  activebackground=T.BG_HOVER, activeforeground=T.FG).pack(side="left", padx=T.PAD_S)
        tk.Button(buttons, text="Сохранить", command=self._save, bg=T.ACCENT, fg="white",
                  relief="flat", font=T.FONT_HEAD, padx=18, pady=6,
                  activebackground=T.ACCENT_HOVER, activeforeground="white").pack(side="left")

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
            acc.symbols = [s.strip().upper() for s in
                           self.entries["symbols"].get().split(",") if s.strip()]
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
        self.result = acc
        self.destroy()


class TraderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Trader — управление счетами MT5")
        self.geometry("1180x720")
        self.minsize(980, 620)
        self.configure(bg=T.BG)

        self.store = AccountStore()
        self.store.load()
        self.supervisor = Supervisor()
        self.selected_login: int | None = None
        self.rows: dict[int, AccountRow] = {}

        self._build()
        self._rebuild_account_list()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(UI_REFRESH_MS, self._tick)

    # ---------------- построение окна ----------------
    def _build(self):
        header = tk.Frame(self, bg=T.BG)
        header.pack(fill="x", padx=T.PAD_L, pady=(T.PAD_L, T.PAD))

        tk.Label(header, text="Trader", bg=T.BG, fg=T.FG,
                 font=T.FONT_TITLE).pack(side="left")
        self.summary = tk.Label(header, text="", bg=T.BG, fg=T.FG_MUTED, font=T.FONT_SMALL)
        self.summary.pack(side="left", padx=T.PAD)

        self.panic = tk.Button(header, text="Закрыть всё на всех счетах",
                               command=self._panic, bg=T.LOSS, fg="white", relief="flat",
                               font=T.FONT_HEAD, padx=16, pady=7,
                               activebackground="#c94438", activeforeground="white")
        self.panic.pack(side="right")

        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True, padx=T.PAD_L, pady=(0, T.PAD_L))

        # ---- левая колонка: счета ----
        left = tk.Frame(body, bg=T.BG, width=330)
        left.pack(side="left", fill="y", padx=(0, T.PAD))
        left.pack_propagate(False)

        bar = tk.Frame(left, bg=T.BG)
        bar.pack(fill="x", pady=(0, T.PAD_S))
        tk.Label(bar, text="СЧЕТА", bg=T.BG, fg=T.FG_DIM, font=T.FONT_SMALL).pack(side="left")
        tk.Button(bar, text="+ Добавить", command=self._add_account, bg=T.BG_CARD, fg=T.FG,
                  relief="flat", font=T.FONT_SMALL, padx=10, pady=4,
                  activebackground=T.BG_HOVER, activeforeground=T.FG).pack(side="right")

        self.list_frame = tk.Frame(left, bg=T.BG)
        self.list_frame.pack(fill="both", expand=True)

        # Управление самим счётом держим рядом со списком счетов, а не среди
        # торговых кнопок — это разные по смыслу действия
        manage = tk.Frame(left, bg=T.BG)
        manage.pack(fill="x", pady=(T.PAD_S, 0))
        self._small(manage, "Изменить", self._edit_account)
        self._small(manage, "Удалить", self._delete_account)
        self.btn_enable = self._small(manage, "Выключить", self._toggle_enabled)

        self.empty_hint = tk.Label(
            self.list_frame,
            text="Счетов пока нет.\n\nНажмите «+ Добавить»\nи введите данные\nторгового счёта.",
            bg=T.BG, fg=T.FG_DIM, font=T.FONT_BODY, justify="center")

        # ---- правая колонка ----
        right = tk.Frame(body, bg=T.BG)
        right.pack(side="left", fill="both", expand=True)

        self.cards = tk.Frame(right, bg=T.BG)
        self.cards.pack(fill="x", pady=(0, T.PAD))
        self.card_equity = self._make_card(self.cards, "СРЕДСТВА")
        self.card_profit = self._make_card(self.cards, "ПЛАВАЮЩИЙ РЕЗУЛЬТАТ")
        self.card_day = self._make_card(self.cards, "ЗА ДЕНЬ")
        self.card_pos = self._make_card(self.cards, "ПОЗИЦИЙ")

        self.status_line = tk.Label(right, text="Счёт не выбран", bg=T.BG,
                                    fg=T.FG_MUTED, font=T.FONT_SMALL, anchor="w")
        self.status_line.pack(fill="x", pady=(0, T.PAD_S))

        actions = tk.Frame(right, bg=T.BG)
        actions.pack(fill="x", pady=(0, T.PAD))
        self.btn_start = self._action(actions, "Запустить", self._start_selected, T.ACCENT)
        self.btn_stop = self._action(actions, "Остановить", self._stop_selected, T.BG_CARD)

        # Разделитель: слева управление счётом, справа закрытие позиций
        tk.Frame(actions, bg=T.BORDER, width=1).pack(side="left", fill="y",
                                                     padx=T.PAD_S, pady=2)

        self._action(actions, "Закрыть прибыльные", self._close_profit, T.BG_CARD)
        self._action(actions, "Закрыть убыточные", self._close_loss, T.BG_CARD)
        self._action(actions, "Закрыть все", self._close_all, T.BG_CARD)

        tk.Label(right, text="ОТКРЫТЫЕ ПОЗИЦИИ", bg=T.BG, fg=T.FG_DIM,
                 font=T.FONT_SMALL, anchor="w").pack(fill="x", pady=(T.PAD_S, T.PAD_S))

        self._build_table(right)

    def _make_card(self, master, caption):
        card = tk.Frame(master, bg=T.BG_CARD)
        card.pack(side="left", fill="both", expand=True, padx=(0, T.PAD_S))
        tk.Label(card, text=caption, bg=T.BG_CARD, fg=T.FG_DIM,
                 font=T.FONT_SMALL).pack(anchor="w", padx=T.PAD, pady=(T.PAD, 0))
        value = tk.Label(card, text="—", bg=T.BG_CARD, fg=T.FG, font=T.FONT_BIG_NUM)
        value.pack(anchor="w", padx=T.PAD, pady=(0, T.PAD))
        return value

    def _small(self, master, text, command):
        btn = tk.Button(master, text=text, command=command, bg=T.BG_CARD, fg=T.FG_MUTED,
                        relief="flat", font=T.FONT_SMALL, padx=10, pady=5,
                        activebackground=T.BG_HOVER, activeforeground=T.FG)
        btn.pack(side="left", padx=(0, T.PAD_S))
        return btn

    def _action(self, master, text, command, bg):
        fg = "white" if bg == T.ACCENT else T.FG
        btn = tk.Button(master, text=text, command=command, bg=bg, fg=fg, relief="flat",
                        font=T.FONT_BODY, padx=12, pady=6,
                        activebackground=T.ACCENT_HOVER if bg == T.ACCENT else T.BG_HOVER,
                        activeforeground=fg)
        btn.pack(side="left", padx=(0, T.PAD_S))
        return btn

    def _build_table(self, master):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("T.Treeview", background=T.BG_CARD, fieldbackground=T.BG_CARD,
                        foreground=T.FG, rowheight=28, borderwidth=0, font=T.FONT_BODY)
        style.configure("T.Treeview.Heading", background=T.BG, foreground=T.FG_DIM,
                        relief="flat", font=T.FONT_SMALL)
        style.map("T.Treeview", background=[("selected", T.BG_HOVER)],
                  foreground=[("selected", T.FG)])

        columns = ("symbol", "type", "volume", "open", "current", "sl", "tp", "profit")
        titles = ("Инструмент", "Тип", "Объём", "Вход", "Сейчас", "SL", "TP", "Результат")
        self.table = ttk.Treeview(master, columns=columns, show="headings",
                                  style="T.Treeview", selectmode="browse")
        for col, title in zip(columns, titles):
            self.table.heading(col, text=title)
            self.table.column(col, anchor="center",
                              width=110 if col in ("symbol", "profit") else 88)
        self.table.pack(fill="both", expand=True)
        self.table.tag_configure("profit", foreground=T.PROFIT)
        self.table.tag_configure("loss", foreground=T.LOSS)
        self.table.bind("<Double-1>", self._close_selected_position)

        hint = tk.Label(master, text="Двойной щелчок по строке закрывает позицию",
                        bg=T.BG, fg=T.FG_DIM, font=T.FONT_SMALL, anchor="w")
        hint.pack(fill="x", pady=(T.PAD_S, 0))

    # ---------------- список счетов ----------------
    def _rebuild_account_list(self):
        for row in self.rows.values():
            row.destroy()
        self.rows.clear()
        self.empty_hint.pack_forget()

        if not self.store.accounts:
            self.empty_hint.pack(expand=True)
            return

        for account in self.store.accounts:
            row = AccountRow(self.list_frame, account, self._select)
            row.pack(fill="x", pady=(0, 2))
            self.rows[account.login] = row

        if self.selected_login not in self.rows:
            self.selected_login = self.store.accounts[0].login
        self._select(self.selected_login)

    def _select(self, login: int):
        self.selected_login = login
        for lg, row in self.rows.items():
            row.set_selected(lg == login)
        self._refresh_details()

    def _selected_account(self) -> Account | None:
        if self.selected_login is None:
            return None
        return self.store.find(self.selected_login)

    # ---------------- действия ----------------
    def _add_account(self):
        dialog = AccountDialog(self)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        if self.store.find(dialog.result.login):
            messagebox.showerror("Счёт уже есть",
                                 f"Счёт {dialog.result.login} уже в списке.")
            return
        self.store.add(dialog.result)
        self.selected_login = dialog.result.login
        self._rebuild_account_list()

    def _edit_account(self):
        account = self._selected_account()
        if account is None:
            return
        if self.supervisor.is_running(account.login):
            messagebox.showwarning("Счёт запущен",
                                   "Сначала остановите счёт, потом меняйте настройки.")
            return
        dialog = AccountDialog(self, account)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        self.store.remove(account.login)
        self.store.add(dialog.result)
        self.selected_login = dialog.result.login
        self._rebuild_account_list()

    def _delete_account(self):
        account = self._selected_account()
        if account is None:
            return
        if not messagebox.askyesno("Удалить счёт",
                                   f"Удалить {account.display()} из списка?"):
            return
        self.supervisor.stop(account.login)
        self.store.remove(account.login)
        self.selected_login = None
        self._rebuild_account_list()

    def _toggle_enabled(self):
        account = self._selected_account()
        if account is None:
            return
        if self.supervisor.is_running(account.login):
            messagebox.showwarning("Счёт запущен", "Сначала остановите счёт.")
            return
        account.enabled = not account.enabled
        self.store.save()
        self._rebuild_account_list()

    def _start_selected(self):
        account = self._selected_account()
        if account is None:
            return
        ok, message = self.supervisor.start(account)
        if not ok:
            messagebox.showerror("Не удалось запустить", message)

    def _stop_selected(self):
        if self.selected_login is not None:
            self.supervisor.stop(self.selected_login)

    def _close_profit(self):
        if self.selected_login is not None:
            self.supervisor.close_profitable(self.selected_login)

    def _close_loss(self):
        if self.selected_login is not None:
            self.supervisor.close_losing(self.selected_login)

    def _close_all(self):
        if self.selected_login is not None:
            self.supervisor.close_all(self.selected_login)

    def _close_selected_position(self, _event=None):
        item = self.table.focus()
        if not item or self.selected_login is None:
            return
        ticket = self.table.item(item, "tags")
        for tag in ticket:
            if tag.startswith("ticket:"):
                self.supervisor.close_ticket(self.selected_login, int(tag.split(":")[1]))
                return

    def _panic(self):
        if not messagebox.askyesno(
            "Закрыть всё",
            "Закрыть ВСЕ позиции на ВСЕХ запущенных счетах?\n\nЭто действие нельзя отменить."
        ):
            return
        sent = self.supervisor.close_all_everywhere()
        messagebox.showinfo("Команда отправлена",
                            f"Команда закрытия отправлена на счетов: {sent}")

    # ---------------- обновление ----------------
    def _tick(self):
        self.supervisor.pump()
        for login, row in self.rows.items():
            row.update_state(self.supervisor.state(login))
        self._refresh_details()
        self._refresh_summary()
        self.after(UI_REFRESH_MS, self._tick)

    def _refresh_summary(self):
        totals = self.supervisor.totals()
        self.summary.configure(
            text=f"запущено счетов: {totals['running']} · "
                 f"подключено: {totals['connected']} · "
                 f"позиций: {totals['positions']} · "
                 f"общий результат: {T.money(totals['profit'])}")

    def _refresh_details(self):
        account = self._selected_account()
        if account is None:
            self.status_line.configure(text="Счёт не выбран", fg=T.FG_MUTED)
            for card in (self.card_equity, self.card_profit, self.card_day, self.card_pos):
                card.configure(text="—", fg=T.FG)
            self.table.delete(*self.table.get_children())
            return

        state = self.supervisor.state(account.login)
        running = self.supervisor.is_running(account.login)

        status = state.status
        if running and self.supervisor.is_stale(account.login):
            status = "нет ответа от процесса"
        text = f"{account.display()} — {status}"
        if state.error:
            text += f" · {state.error}"
        if state.trading_blocked:
            text += f" · ТОРГОВЛЯ ОСТАНОВЛЕНА: {state.blocked_reason}"
        self.status_line.configure(text=text, fg=T.status_color(status))

        # Неактивная кнопка не должна выглядеть активной: гасим и фон тоже
        self.btn_start.configure(state="disabled" if running else "normal",
                                 bg=T.BG_CARD if running else T.ACCENT,
                                 fg=T.FG_DIM if running else "white")
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.btn_enable.configure(text="Выключить" if account.enabled else "Включить")

        if state.connected:
            self.card_equity.configure(text=f"{state.equity:,.2f}".replace(",", " "), fg=T.FG)
            self.card_profit.configure(text=T.money(state.profit), fg=T.money_color(state.profit))
            self.card_day.configure(text=f"{state.daily_pct:+.2f}%",
                                    fg=T.money_color(state.daily_pct))
            self.card_pos.configure(text=str(len(state.positions)), fg=T.FG)
        else:
            for card in (self.card_equity, self.card_profit, self.card_day, self.card_pos):
                card.configure(text="—", fg=T.FG_DIM)

        self._refresh_table(state)

    def _refresh_table(self, state):
        self.table.delete(*self.table.get_children())
        for p in state.positions:
            tag = "profit" if p["profit"] > 0 else "loss" if p["profit"] < 0 else ""
            self.table.insert(
                "", "end",
                values=(p["symbol"], p["type"], f"{p['volume']:.2f}",
                        f"{p['price_open']:.5f}".rstrip("0").rstrip("."),
                        f"{p['price_current']:.5f}".rstrip("0").rstrip("."),
                        f"{p['sl']:.5f}".rstrip("0").rstrip(".") if p["sl"] else "—",
                        f"{p['tp']:.5f}".rstrip("0").rstrip(".") if p["tp"] else "—",
                        T.money(p["profit"])),
                tags=(tag, f"ticket:{p['ticket']}"))

    def _on_close(self):
        if self.supervisor.totals()["running"] > 0:
            if not messagebox.askyesno(
                "Выход",
                "Есть запущенные счета. Остановить их и выйти?\n\n"
                "Открытые позиции при этом НЕ закрываются."
            ):
                return
        self.supervisor.stop_all()
        self.destroy()


def main():
    TraderApp().mainloop()
