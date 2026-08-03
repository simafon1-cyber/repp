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


def login_preflight() -> str:
    """Что нужно ИМЕННО ДЛЯ ВХОДА. Возвращает "" если всё готово.

    Намеренно НЕ требует TELEGRAM_ENABLED и списка источников: войти логично
    до того, как включать чтение, и раньше эта проверка отказывала человеку,
    который просто нажал «Войти в Telegram» первым делом."""
    api_id, api_hash = credentials()
    if not api_id or not api_hash:
        return ("Не заданы api_id / api_hash. Впишите их на вкладке «Источники». "
                "Выдают бесплатно на https://my.telegram.org -> API development tools.")
    # find_spec, а не import: проверить наличие библиотеки нужно, а тащить её
    # в память на каждый вызов — нет.
    if importlib.util.find_spec("telethon") is None:
        return ("Не установлена библиотека telethon. Установите её командой: "
                "pip install telethon")
    return ""


def preflight() -> str:
    """Что нужно для ЧТЕНИЯ сообщений в фоне."""
    if not tgs.enabled():
        return "Telegram выключен в настройках (TELEGRAM_ENABLED = False)."
    problem = login_preflight()
    if problem:
        return problem
    if not sources():
        return "Не указано ни одного источника (TELEGRAM_SOURCES)."
    return ""


def explain_error(exc: Exception) -> str:
    """Ошибку Telethon — в понятную фразу.

    Без этого человек видел английское имя класса исключения и не мог понять,
    что именно от него хотят."""
    name = type(exc).__name__
    text = str(exc)

    known = {
        "PhoneNumberInvalidError":
            "Неверный номер телефона. Нужен международный формат, например +79991234567.",
        "PhoneNumberBannedError":
            "Этот номер заблокирован в Telegram.",
        "PhoneCodeInvalidError":
            "Неверный код подтверждения. Проверьте и попробуйте ещё раз.",
        "PhoneCodeExpiredError":
            "Код устарел — Telegram даёт на ввод ограниченное время. Начните вход заново.",
        "SessionPasswordNeededError":
            "У аккаунта включена двухфакторная защита — нужен облачный пароль Telegram.",
        "PasswordHashInvalidError":
            "Неверный пароль двухфакторной защиты.",
        "ApiIdInvalidError":
            "Неверные api_id / api_hash. Проверьте их на my.telegram.org.",
        "AuthKeyDuplicatedError":
            "Файл сессии использовался на другом компьютере. Удалите telegram_session "
            "рядом с программой и войдите заново.",
    }
    if name in known:
        return known[name]
    if name == "FloodWaitError":
        seconds = getattr(exc, "seconds", 0)
        return (f"Telegram временно ограничил попытки входа. Подождите "
                f"{seconds // 60 + 1} мин и попробуйте снова.")
    if isinstance(exc, TypeError) and "awaitable" in text:
        # Ровно та ошибка, из-за которой вход не работал вовсе: результат
        # client.start() оборачивался в run_until_complete второй раз.
        return ("Внутренняя ошибка запуска Telegram-клиента. Обновите программу "
                "до последней версии.")
    if isinstance(exc, (OSError, ConnectionError)):
        return f"Нет связи с Telegram: {text}"
    return f"{name}: {text}" if text else name


def _run():
    """Тело фонового потока: свой цикл событий, свой клиент Telethon."""
    import asyncio

    from telethon import TelegramClient, events

    api_id, api_hash = credentials()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # loop= не передаём: параметр объявлен устаревшим, клиент сам берёт
    # текущий цикл событий потока.
    client = TelegramClient(session_path(), api_id, api_hash)

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
            # disconnect() без обёртки: цикл сейчас НЕ запущен, поэтому
            # Telethon крутит его сам и возвращает None — обернуть значило бы
            # получить run_until_complete(None) и TypeError.
            client.disconnect()
            return

        tgs.set_status(True, f"Подключено. Источники: {', '.join(sources())}")
        log.info("Telegram: чтение запущено, источники: %s", ", ".join(sources()))

        while not _stop.is_set():
            loop.run_until_complete(asyncio.sleep(1))

        client.disconnect()
    except Exception as e:
        tgs.set_status(False, explain_error(e))
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

    ВЫЗЫВАТЬ ИЗ ФОНОВОГО ПОТОКА. Функция блокирует поток на всё время входа —
    включая ожидание, пока человек введёт код. Если запустить её в потоке
    интерфейса, окно замрёт и диалог ввода кода может не показаться вовсе.

    Отдельная функция, а не часть start(): просить код посреди запуска
    торгового цикла, где никто его не увидит, — верный способ получить
    зависший запуск."""
    problem = login_preflight()
    if problem:
        return problem

    import asyncio

    from telethon import TelegramClient

    api_id, api_hash = credentials()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = TelegramClient(session_path(), api_id, api_hash)

    async def do_login():
        # ВАЖНО: и start(), и disconnect() у Telethon двухрежимные — если цикл
        # событий НЕ запущен, они сами его крутят и возвращают уже готовый
        # результат, а не корутину. Раньше здесь стояло
        # loop.run_until_complete(client.start(...)), и внутрь попадал уже
        # объект клиента -> TypeError "a coroutine or an awaitable is required".
        # Вход не работал НИКОГДА. Внутри async-функции цикл запущен, поэтому
        # start() отдаёт корутину и await корректен.
        await client.start(
            phone=lambda: phone,
            code_callback=code_callback,
            password=password_callback or (lambda: ""),
        )
        await client.disconnect()

    try:
        loop.run_until_complete(do_login())
        return ""
    except Exception as e:
        log.warning("Telegram: вход не удался: %s: %s", type(e).__name__, e)
        return explain_error(e)
    finally:
        try:
            loop.close()
        except Exception:
            pass
