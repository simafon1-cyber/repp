"""Минималистичная тёмная тема: цвета, шрифты, отступы.

Всё оформление собрано здесь, чтобы менять вид в одном месте.
Палитра спокойная: тёмно-серый фон, один акцентный цвет, зелёный и красный
только для денег — так глаз сразу находит главное.
"""

# Фоны: три уровня глубины, без резких границ
BG = "#16181d"          # фон окна
BG_CARD = "#1e2128"     # карточки и панели
BG_HOVER = "#262a33"    # подсветка под курсором
BORDER = "#2c313c"

# Текст
FG = "#e6e8ec"          # основной
FG_MUTED = "#8b929f"    # подписи, второстепенное
FG_DIM = "#5d6472"      # совсем тихое

# Акцент — один на всё приложение
ACCENT = "#4c8dff"
ACCENT_HOVER = "#3d7ae8"

# Деньги
PROFIT = "#3fb950"
LOSS = "#f0574a"
WARNING = "#d9a441"

# Состояния счёта
STATUS_COLORS = {
    "подключён": PROFIT,
    "запускается": WARNING,
    "остановлен": FG_DIM,
    "не запущен": FG_DIM,
    "ошибка": LOSS,
    "ошибка входа": LOSS,
    "нет связи": LOSS,
    "аварийная остановка": LOSS,
}

# Шрифты (кортежи для tkinter)
FONT_TITLE = ("Segoe UI Semibold", 15)
FONT_HEAD = ("Segoe UI Semibold", 11)
FONT_BODY = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_MONO = ("Cascadia Mono", 10)
FONT_BIG_NUM = ("Segoe UI Light", 26)

# Отступы — кратны 4, чтобы всё выравнивалось само собой
PAD = 12
PAD_S = 6
PAD_L = 20
RADIUS = 10


def status_color(status: str) -> str:
    return STATUS_COLORS.get(status, FG_MUTED)


def money_color(value: float) -> str:
    if value > 0:
        return PROFIT
    if value < 0:
        return LOSS
    return FG_MUTED


def money(value: float, digits: int = 2) -> str:
    """Форматирует деньги со знаком: +12.34 / -5.00."""
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.{digits}f}".replace(",", " ")
