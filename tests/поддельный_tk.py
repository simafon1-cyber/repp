"""поддельный_tk.py — окно программы БЕЗ окна.

ЗАЧЕМ

Владелец: «все кнопки протестируй». Проверить кнопку по-настоящему —
значит НАЖАТЬ её и посмотреть, что произойдёт. Разбор исходника этого не
даёт: он видит, что обработчик существует, но не видит, что внутри он
обращается к полю, которого ещё нет, или делит на ноль.

В среде проверки tkinter не установлен вовсе, и настоящее окно там не
поднимется никогда. Поэтому здесь подделка: она принимает всё, что
принимает tkinter, ничего не рисует и ЗАПОМИНАЕТ каждую созданную
кнопку вместе с её обработчиком. После сборки окна по этому списку можно
пройтись и нажать всё подряд.

ЧЕСТНАЯ ОГОВОРКА

Это НЕ проверка того, что окно выглядит правильно. Подделка не считает
пиксели и не рисует. Она отвечает ровно на два вопроса: собирается ли
окно и падает ли обработчик при нажатии. Всё остальное — за другими
тестами.
"""

from __future__ import annotations

import types

# Сюда попадает каждая созданная кнопка: (подпись, обработчик, вид).
КНОПКИ = []
# Каждое показанное человеку окошко сообщения: (вид, заголовок, текст).
СООБЩЕНИЯ = []
# Ответ, который «нажмёт» человек в окне вопроса. Меняется тестом.
ОТВЕТ_ЧЕЛОВЕКА = {"да": False}


def сброс():
    КНОПКИ.clear()
    СООБЩЕНИЯ.clear()
    ОТВЕТ_ЧЕЛОВЕКА["да"] = False


class Виджет:
    """Принимает что угодно, ничего не делает, возвращает себя."""

    def __init__(self, *args, **kwargs):
        self._свойства = dict(kwargs)
        self._дети = []
        # Кнопку запоминаем: по ней потом будут «нажимать».
        вид = type(self).__name__
        if вид in ("Button", "Checkbutton", "Radiobutton"):
            КНОПКИ.append({
                "подпись": str(kwargs.get("text", "")),
                "обработчик": kwargs.get("command"),
                "вид": вид,
            })

    # --- любое неизвестное обращение безопасно ---
    def __getattr__(self, имя):
        # СЛУЖЕБНЫЕ ИМЕНА НЕ ПОДДЕЛЫВАЕМ. Если отвечать «да» и на них,
        # питон решит, что подделку можно перебирать, и `что-то in виджет`
        # уйдёт в бесконечный перебор. Именно на этом сборка окна и
        # зависла: `str(frame) in book.tabs()`.
        if имя.startswith("__") and имя.endswith("__"):
            raise AttributeError(имя)

        def что_угодно(*a, **k):
            return self
        return что_угодно

    def tabs(self):
        """Notebook.tabs() обязан вернуть перечень, а не виджет."""
        return tuple()

    def state(self, *a, **k):
        return ("!disabled",)

    def get(self, *a, **k):
        return ""

    def curselection(self):
        return ()

    def index(self, *a, **k):
        return 0

    def __setitem__(self, ключ, значение):
        self._свойства[ключ] = значение

    def __getitem__(self, ключ):
        return self._свойства.get(ключ, "")

    def cget(self, ключ):
        return self._свойства.get(ключ, "")

    def configure(self, *args, **kwargs):
        # ttk.Style.configure(".", background=...) — первым идёт ИМЯ стиля.
        # Настоящий tkinter это принимает, значит и подделка обязана.
        self._свойства.update(kwargs)
        return self

    config = configure

    def keys(self):
        return list(self._свойства)

    def winfo_ismapped(self):
        return True

    def winfo_children(self):
        return list(self._дети)

    def winfo_exists(self):
        return True

    def winfo_width(self):
        return 1040

    def winfo_height(self):
        return 720

    def bbox(self, *a, **k):
        return (0, 0, 1040, 720)

    # Планировщик НИЧЕГО не выполняет: иначе сборка окна утянула бы за
    # собой подключение к терминалу, обновление и сеть. Нам нужно окно,
    # а не запущенная программа.
    def after(self, *a, **k):
        return "задача"

    def after_cancel(self, *a, **k):
        return None

    def mainloop(self, *a, **k):
        return None


class Корень(Виджет):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.tk = types.SimpleNamespace(call=lambda *args: "")


class Переменная:
    def __init__(self, master=None, value=None, name=None):
        self._значение = value

    def get(self):
        return self._значение

    def set(self, значение):
        self._значение = значение

    def trace_add(self, *a, **k):
        return "след"

    trace = trace_add


class Строка(Переменная):
    def __init__(self, master=None, value="", name=None):
        super().__init__(master, "" if value is None else value, name)


class Флаг(Переменная):
    def __init__(self, master=None, value=False, name=None):
        super().__init__(master, bool(value), name)


class Число(Переменная):
    def __init__(self, master=None, value=0, name=None):
        super().__init__(master, 0 if value is None else value, name)


class Таблица(Виджет):
    """Treeview: нужен именно список строк, иначе обработчики падают."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._строки = {}

    def get_children(self, *a, **k):
        return tuple(self._строки)

    def insert(self, parent, index, iid=None, **k):
        iid = iid or f"строка{len(self._строки)}"
        self._строки[iid] = k.get("values", ())
        return iid

    def item(self, iid, опция=None, **k):
        значения = self._строки.get(iid, ())
        if опция == "values":
            return значения
        if опция == "tags":
            return ()
        return {"values": значения, "tags": ()}

    def delete(self, *iids):
        for iid in iids:
            self._строки.pop(iid, None)

    def selection(self):
        return ()

    def focus(self, *a):
        return ""

    def exists(self, iid):
        return iid in self._строки


def _окошко(вид):
    def показать(заголовок="", текст="", **k):
        СООБЩЕНИЯ.append({"вид": вид, "заголовок": заголовок, "текст": текст})
        if вид in ("askyesno", "askokcancel"):
            return ОТВЕТ_ЧЕЛОВЕКА["да"]
        return None
    return показать


def собрать_модули():
    """Вернуть (tkinter, tkinter.ttk, tkinter.messagebox, ...) — подделки."""
    tk = types.ModuleType("tkinter")
    for имя in ("Frame", "Label", "Button", "Entry", "Text", "Canvas",
                "Checkbutton", "Radiobutton", "Scrollbar", "Toplevel",
                "Menu", "PhotoImage", "Listbox", "Spinbox", "Scale",
                "PanedWindow", "LabelFrame", "Message"):
        setattr(tk, имя, type(имя, (Виджет,), {}))
    tk.Tk = Корень
    tk.StringVar, tk.BooleanVar, tk.IntVar, tk.DoubleVar = (
        Строка, Флаг, Число, Число)
    tk.Variable = Переменная
    tk.TclError = type("TclError", (Exception,), {})
    tk.END = "end"
    tk.NORMAL, tk.DISABLED = "normal", "disabled"
    tk.LEFT, tk.RIGHT, tk.TOP, tk.BOTTOM, tk.BOTH, tk.X, tk.Y = (
        "left", "right", "top", "bottom", "both", "x", "y")
    tk.W, tk.E, tk.N, tk.S = "w", "e", "n", "s"

    ttk = types.ModuleType("tkinter.ttk")
    for имя in ("Frame", "Label", "Button", "Entry", "Combobox", "Notebook",
                "Scrollbar", "Separator", "Checkbutton", "Radiobutton",
                "LabelFrame", "Progressbar", "Scale", "Sizegrip",
                "Panedwindow", "PanedWindow", "Spinbox", "Menubutton"):
        setattr(ttk, имя, type(имя, (Виджет,), {}))
    ttk.Treeview = Таблица

    class Стиль(Виджет):
        def theme_use(self, *a, **k):
            return "clam"

        def theme_names(self):
            return ("clam", "default")

        def lookup(self, *a, **k):
            return ""

    ttk.Style = Стиль
    tk.ttk = ttk

    mb = types.ModuleType("tkinter.messagebox")
    for имя in ("showinfo", "showwarning", "showerror", "askyesno",
                "askokcancel", "askquestion", "askretrycancel"):
        setattr(mb, имя, _окошко(имя))

    fd = types.ModuleType("tkinter.filedialog")
    fd.askopenfilename = lambda **k: ""
    fd.asksaveasfilename = lambda **k: ""
    fd.askdirectory = lambda **k: ""

    sd = types.ModuleType("tkinter.simpledialog")
    sd.askstring = lambda *a, **k: ""

    ft = types.ModuleType("tkinter.font")
    ft.Font = type("Font", (Виджет,), {"measure": lambda self, s: len(s) * 7,
                                       "metrics": lambda self, *a: 16})
    ft.nametofont = lambda имя: ft.Font()
    ft.families = lambda *a, **k: ("Segoe UI",)

    return {"tkinter": tk, "tkinter.ttk": ttk, "tkinter.messagebox": mb,
            "tkinter.filedialog": fd, "tkinter.simpledialog": sd,
            "tkinter.font": ft}
