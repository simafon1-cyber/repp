"""ui_theme.py — оформление окна программы: светлая и тёмная тема.

ЗАЧЕМ
Владелец: «переделай внешний вид приложения, на чёрном фоне очень плохо всё
видно». Раньше тема была одна — тёмная, и цвета в ней были подобраны на
глаз: серый текст #9a9a9a на почти чёрном фоне #1b1b1b, подписи #666 —
такое читается только на хорошем мониторе в тёмной комнате.

ЧТО ИЗМЕНИЛОСЬ
  1. По умолчанию тема СВЕТЛАЯ — на светлом фоне текст читается при любом
     освещении, а таблицы с цифрами (а их тут почти на каждой вкладке)
     разбирать заметно легче.
  2. Тёмная осталась и стала контрастнее: подписи больше не тонут в фоне.
  3. Цвета собраны В ОДНОМ месте. Раньше они были вписаны прямо в код в
     70 местах, и поменять оформление означало пройти их все руками.

ПРО КОНТРАСТ
У каждой пары «текст на фоне» посчитан контраст по формуле WCAG (см.
contrast()). Обычный текст должен иметь не меньше 4.5, приглушённые
подписи — не меньше 3.0. Это не украшательство: ровно из-за низкого
контраста и возникла жалоба. Проверяется тестами, чтобы «красивый» оттенок
случайно не вернул нечитаемый текст.
"""

import logging

log = logging.getLogger("ui_theme")

# --- Светлая тема (по умолчанию) ---
LIGHT = {
    "name": "light",
    "bg": "#f4f5f7",          # фон окна
    "card": "#ffffff",        # поля ввода, таблицы
    "fg": "#14161a",          # основной текст — почти чёрный
    "muted": "#4a5058",       # подписи и пояснения
    "dim": "#6b727b",         # совсем второстепенное
    "accent": "#1a5fd0",      # кнопки действия
    "accent_fg": "#ffffff",
    "profit": "#0f7a34",      # прибыль
    "loss": "#c02626",        # убыток
    "warning": "#8a5a00",     # предупреждение
    "tab_bg": "#e3e6ea",      # неактивная вкладка
    "tab_active": "#ffffff",  # активная вкладка
    "heading": "#e3e6ea",     # шапка таблицы
    "border": "#c6cbd2",
    "row_alt": "#f7f8fa",     # чередование строк таблицы
}

# --- Тёмная тема (стала контрастнее прежней) ---
DARK = {
    "name": "dark",
    "bg": "#1f2226",
    "card": "#2a2e34",
    "fg": "#f2f4f7",          # было #eee на #1b1b1b
    "muted": "#b8c0ca",       # было #9a9a9a — тонуло в фоне
    "dim": "#98a1ac",         # было #666 — почти не читалось
    "accent": "#5b9dff",
    "accent_fg": "#0d1015",
    "profit": "#57d977",
    "loss": "#ff7b72",
    "warning": "#e8b563",
    "tab_bg": "#2a2e34",
    "tab_active": "#3a4048",
    "heading": "#343a42",
    "border": "#454c55",
    "row_alt": "#262a30",
}

THEMES = {"light": LIGHT, "dark": DARK}
DEFAULT = "light"


def palette(name: str = "") -> dict:
    """Набор цветов по имени темы. Незнакомое имя — светлая тема, а не
    падение: из-за опечатки в настройках окно открыться обязано."""
    key = str(name or "").strip().lower()
    if key not in THEMES:
        if key:
            log.warning("Неизвестная тема оформления %r — беру светлую.", name)
        key = DEFAULT
    return THEMES[key]


def from_config(cfg_module) -> dict:
    return palette(getattr(cfg_module, "UI_THEME", DEFAULT))


# ---------------------------------------------------------------------
# Контраст по WCAG. Нужен не для красоты: жалоба владельца была именно
# про читаемость, и без числа «стало лучше» — это вкусовщина.
# ---------------------------------------------------------------------
def _channel(value: float) -> float:
    v = value / 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    """Относительная яркость цвета #rrggbb (0 — чёрный, 1 — белый)."""
    text = color.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)   # #fff -> #ffffff
    if len(text) != 6:
        raise ValueError(f"Ожидался цвет вида #rrggbb, получено {color!r}")
    r, g, b = (int(text[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(foreground: str, background: str) -> float:
    """Во сколько раз текст контрастнее фона. 1.0 — сливается полностью,
    21.0 — чёрное на белом. Порог читаемости обычного текста — 4.5."""
    a, b = luminance(foreground), luminance(background)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


# Что с чем сравнивается и какой контраст обязателен. Проверяется тестом.
CONTRAST_RULES = [
    ("fg", "bg", 4.5),        # основной текст
    ("fg", "card", 4.5),      # текст в полях и таблицах
    ("muted", "bg", 4.5),     # подписи — их тут много, и они важны
    ("dim", "bg", 3.0),       # второстепенное
    ("profit", "bg", 3.0),
    ("loss", "bg", 3.0),
    ("warning", "bg", 3.0),
    ("accent_fg", "accent", 4.5),   # текст на кнопке
    ("fg", "tab_active", 4.5),
    ("fg", "heading", 4.5),
]


def contrast_problems(colors: dict) -> list:
    """Список пар, которые читаются плохо. Пустой список — всё в порядке."""
    problems = []
    for fg_key, bg_key, required in CONTRAST_RULES:
        value = contrast(colors[fg_key], colors[bg_key])
        if value < required:
            problems.append(
                f"{fg_key} на {bg_key}: контраст {value:.1f}, нужно {required}")
    return problems


def apply(root, style, colors: dict) -> None:
    """Применить палитру к окну и ко всем стандартным виджетам ttk."""
    bg, card, fg = colors["bg"], colors["card"], colors["fg"]
    root.configure(bg=bg)

    style.configure(".", background=bg, foreground=fg, fieldbackground=card,
                    bordercolor=colors["border"])
    style.configure("TFrame", background=bg)
    style.configure("TLabel", background=bg, foreground=fg)
    style.configure("TLabelframe", background=bg, foreground=fg,
                    bordercolor=colors["border"])
    style.configure("TLabelframe.Label", background=bg, foreground=fg)
    style.configure("TCheckbutton", background=bg, foreground=fg)
    style.configure("TRadiobutton", background=bg, foreground=fg)
    style.configure("TEntry", fieldbackground=card, foreground=fg,
                    insertcolor=fg)
    style.configure("TCombobox", fieldbackground=card, foreground=fg)

    style.configure("TButton", background=colors["tab_bg"], foreground=fg,
                    padding=(10, 5), borderwidth=1)
    style.map("TButton",
              background=[("active", colors["tab_active"]),
                          ("pressed", colors["heading"])])

    style.configure("TNotebook", background=bg, borderwidth=0)
    style.configure("TNotebook.Tab", background=colors["tab_bg"],
                    foreground=fg, padding=(14, 7))
    style.map("TNotebook.Tab",
              background=[("selected", colors["tab_active"])],
              foreground=[("selected", fg)])

    # Строки таблиц повыше: цифры перестают слипаться, а в таблицах здесь
    # почти всё содержимое программы
    style.configure("Treeview", background=card, fieldbackground=card,
                    foreground=fg, rowheight=26, borderwidth=0)
    style.configure("Treeview.Heading", background=colors["heading"],
                    foreground=fg, relief="flat", padding=(6, 4))
    style.map("Treeview", background=[("selected", colors["accent"])],
              foreground=[("selected", colors["accent_fg"])])

    style.configure("Horizontal.TProgressbar", background=colors["accent"],
                    troughcolor=card)
