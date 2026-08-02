"""
telegram_reader.py — чтение сообщений из Telegram в фоне.

ПОЧЕМУ НЕ БОТ, А ВХОД ПОД ВАШИМ АККАУНТОМ
-----------------------------------------
Telegram запрещает ботам видеть сообщения других ботов — прямая цитата из
официального FAQ: "Bots will not be able to see messages from other bots
regardless of mode" (core.telegram.org/bots/faq). Сигналы от чужого бота
приходят В ВАШ личный чат с ним, и прочитать их может только клиент,
вошедший под вашим аккаунтом.

Поэтому используется Telethon (клиентский API Telegram, MTProto). Нужны:
  * api_id и api_hash — бесплатно на https://my.telegram.org -> API development tools
  * одноразовый вход по номеру телефона и коду из Telegram

После первого входа создаётся файл сессии, и код больше не спрашивают.

ЧТО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ
-------------------------
Не отправляет сообщения, не вступает в чаты, не подписывается ни на что и не
трогает торговлю. Только читает указанные источники и передаёт текст в
telegram_signals.py, где действуют жёсткие ограничения на применение сигнала.

ЕСЛИ TELETHON НЕ УСТАНОВЛЕН ИЛИ ВХОД НЕ ВЫПОЛНЕН
------------------------------------------------
Программа работает как обычно, просто без сигналов из Telegram. Никаких
падений: торговля не должна зависеть от стороннего источника.
"""

import importlib.util
import logging
import threading

import config as cfg
import telegram_signals as tgs

log = logging.getLogger("telegram_reader")

_thread = None
_stop = threading.Event()


def sources() -> list:
    """Список источников: @имена или числовые id чатов."""
    raw = getattr(cfg, "TELEGRAM_SOURCES", []) or []
    out = []
    for item in raw:
        item = str(item).strip()
        if item:
            out.append(item)
    return out


def session_path() -> str:
    return str(getattr(cfg, "TELEGRAM_SESSION_PATH", "telegram_session"))


def credentials():
    api_id = getattr(cfg, "TELEGRAM_API_ID", 0)
    api_hash = getattr(cfg, "TELEGRAM_API_HASH", "")
    try:
        api_id = int(api_id)
    except (TypeError, ValueError):
        api_id = 0
    return api_id, str(api_hash or "")


def preflight() -> str:
    """Проверяет, можно ли вообще запускаться. Возвращает "" если всё в
    порядке, иначе понятную человеку причину отказа."""
    if not tgs.enabled():
        return "Telegram выключен в настройках (TELEGRAM_ENABLED = False)."
    api_id, api_hash = credentials()
    if not api_id or not api_hash:
        return ("Не заданы api_id / api_hash. Их выдают бесплатно на "
                "https://my.telegram.org -> API development tools.")
    if not sources():
        return "Не указано ни одного источника (TELEGRAM_SOURCES)."
    # find_spec, а не import: проверить наличие библиотеки нужно, а тащить её
    # в память на каждый вызов preflight() — нет.
    if importlib.util.find_spec("telethon") is None:
        return ("Не установлена библиотека telethon. Установите её командой: "
                "pip install telethon")
    return ""


def _run():
    """Тело фонового потока: свой цикл событий, свой клиент Telethon."""
    import asyncio

    from telethon import TelegramClient, events

    api_id, api_hash = credentials()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    client = TelegramClient(session_path(), api_id, api_hash, loop=loop)

    @client.on(events.NewMessage(chats=sources()))
    async def handler(event):                       # pragma: no cover - нужен живой Telegram
        text = event.raw_text or ""
        signal = tgs.parse_signal(text)
        if signal is None:
            log.debug("Telegram: сообщение не распознано как сигнал: %.60s", text)
            return
        tgs.remember(signal)
        log.info("Telegram: сигнал %s %s",
                 signal["instrument"],
                 "покупка" if signal["direction"] == tgs.BUY else "продажа")

    try:
        # connect() без интерактивного ввода: если сессии нет, вход должен
        # выполняться отдельной командой (см. login()), а не молча посреди
        # торгового запуска, где никто не увидит запрос кода.
        loop.run_until_complete(client.connect())
        if not loop.run_until_complete(client.is_user_authorized()):
            tgs.set_status(False, "Вход не выполнен — запустите вход по номеру телефона "
                                  "(кнопка «Войти в Telegram» на вкладке «Сигналы»).")
            log.warning("Telegram: сессия не авторизована — чтение не запущено.")
            loop.run_until_complete(client.disconnect())
            return

        tgs.set_status(True, f"Подключено. Источники: {', '.join(sources())}")
        log.info("Telegram: чтение запущено, источники: %s", ", ".join(sources()))

        while not _stop.is_set():
            loop.run_until_complete(asyncio.sleep(1))

        loop.run_until_complete(client.disconnect())
    except Exception as e:
        tgs.set_status(False, f"Ошибка подключения: {e}")
        log.warning("Telegram: чтение остановлено из-за ошибки: %s", e)
    finally:
        tgs.set_status(False, "Отключено")
        try:
            loop.close()
        except Exception:
            pass


def start() -> str:
    """Запускает фоновое чтение. Возвращает "" при успехе или причину отказа."""
    global _thread
    problem = preflight()
    if problem:
        tgs.set_status(False, problem)
        return problem
    if _thread is not None and _thread.is_alive():
        return ""
    _stop.clear()
    _thread = threading.Thread(target=_run, daemon=True, name="telegram-reader")
    _thread.start()
    return ""


def stop() -> None:
    _stop.set()


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def login(phone: str, code_callback, password_callback=None) -> str:
    """Одноразовый вход по номеру телефона. Возвращает "" при успехе.

    code_callback() должен вернуть код, который Telegram пришлёт в приложение;
    password_callback() — пароль двухфакторной защиты, если он включён.
    Отдельная функция, а не часть start(): просить код посреди запуска
    торгового цикла, где никто его не увидит, — верный способ получить
    зависший запуск."""
    problem = preflight()
    if problem and "Вход" not in problem:
        return problem

    import asyncio

    from telethon import TelegramClient

    api_id, api_hash = credentials()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = TelegramClient(session_path(), api_id, api_hash, loop=loop)
    try:
        loop.run_until_complete(client.start(
            phone=lambda: phone,
            code_callback=code_callback,
            password=password_callback or (lambda: ""),
        ))
        loop.run_until_complete(client.disconnect())
        return ""
    except Exception as e:
        return f"Не удалось войти: {e}"
    finally:
        try:
            loop.close()
        except Exception:
            pass
