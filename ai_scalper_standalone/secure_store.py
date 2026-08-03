"""
secure_store.py — шифрование чувствительных настроек в config.py (пароль MT5,
ключи Claude/OpenAI/новостных API) паролем ВХОДА в программу (тем же, что
вводится на экране входа desktop-приложения / в HTTP Basic Auth веб-дашборда).

ЗАЧЕМ: config.py раньше хранил всё это открытым текстом. Если скопировать папку
программы на другой компьютер (или её найдёт посторонний на этом же диске) —
пароли/ключи читались просто открытием файла в блокноте. Теперь эти поля в
config.py хранятся зашифрованными (префикс "enc:"), а ключ шифрования нигде
не хранится — он каждый раз заново считается из пароля, который вводит
пользователь. Без пароля содержимое файла бесполезно.

КАК ЭТО РАБОТАЕТ:
  - Пароль входа (DASHBOARD_LOGIN/DASHBOARD_PASSWORD) больше не хранится в
    config.py открытым текстом — хранится только PBKDF2-хэш
    (DASHBOARD_PASSWORD_HASH), которого достаточно ПРОВЕРИТЬ пароль, но
    невозможно восстановить сам пароль обратно.
  - Остальные секреты (MT5_PASSWORD, ANTHROPIC_API_KEY, OPENAI_API_KEY,
    значения в NEWS_API_KEYS) хранятся в config.py в виде "enc:<токен>" —
    это Fernet (AES128-CBC + HMAC), ключ выводится из пароля входа через
    PBKDF2-HMAC-SHA256 (200 000 итераций) + SECURITY_SALT из config.py (сама
    соль не секрет — нужна только чтобы ключ был устойчивым и одинаковым
    между запусками).
  - Расшифровка происходит ОДИН РАЗ в памяти при входе в программу (или при
    запуске `python main.py` напрямую — тогда пароль спрашивается в консоли).
    На диске секреты остаются зашифрованными всегда.
  - Если забыл пароль входа — секреты восстановить нельзя, это осознанный
    компромисс (иначе защита была бы фиктивной). Придётся вписать новые
    значения (MT5-пароль, ключи AI/новостей) заново через интерфейс программы
    после того, как задашь новый пароль.

Всё в этом модуле — best-effort по отношению к остальной программе: если
что-то в шифровании не получается (нет библиотеки cryptography, повреждён
файл и т.д.) — вызывающий код (desktop_app.py/main.py) обязан поймать
исключение и продолжить работу в старом (нешифрованном) режиме, а не упасть.
"""

import base64
import hashlib
import hmac
import secrets as _secrets

ENC_PREFIX = "enc:"
PBKDF2_ITERATIONS = 200_000
_KEY_LEN = 32


def new_salt() -> str:
    """Случайная соль (не секрет) — генерируется один раз при первой миграции
    и дальше хранится в config.py как SECURITY_SALT."""
    return _secrets.token_hex(16)


def _pbkdf2(password: str, salt_hex: str, length: int = _KEY_LEN) -> bytes:
    salt = bytes.fromhex(salt_hex) if salt_hex else b""
    return hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=length)


def hash_password(password: str, salt_hex: str) -> str:
    """Для DASHBOARD_PASSWORD_HASH — необратимый хэш, годится только для
    сравнения при входе, не для восстановления пароля."""
    return _pbkdf2(password, salt_hex).hex()


def verify_password(password: str, salt_hex: str, stored_hash_hex: str) -> bool:
    if not stored_hash_hex:
        return False
    computed = hash_password(password, salt_hex)
    return hmac.compare_digest(computed, stored_hash_hex)


def _derive_fernet_key(password: str, salt_hex: str) -> bytes:
    raw = _pbkdf2(password, salt_hex, length=32)
    return base64.urlsafe_b64encode(raw)


def private_mode() -> bool:
    """Приватный режим: репозиторий закрытый, секреты лежат в config.py
    открытым текстом.

    Зачем он вообще нужен. Шифрование секретов держится на пароле входа: без
    него расшифровать нечего. Как только вход отключён (REQUIRE_LOGIN = False),
    получается тупик — ключи в файле есть, а открыть их нечем. Приватный режим
    убирает этот тупик честно: не притворяется, что шифрует, а прямо говорит,
    что защита теперь одна — закрытый репозиторий и ваш компьютер.

    Что он НЕ отменяет: telegram_session, accounts.json, журналы и CSV сделок
    остаются вне git при любом режиме (см. .gitignore)."""
    try:
        import config as cfg
    except Exception:
        return False
    return bool(getattr(cfg, "PRIVATE_MODE", False))


def protect_secret(plaintext: str, password: str, salt_hex: str) -> str:
    """Как секрет должен лечь в config.py — единая точка на всю программу.

    В приватном режиме возвращает значение как есть, иначе шифрует. Раньше
    каждое место вызывало encrypt_value() напрямую, и добавить режим означало
    бы править их поодиночке — забыть одно место было делом времени."""
    if private_mode():
        return plaintext
    return encrypt_value(plaintext, password, salt_hex)


def encrypt_value(plaintext: str, password: str, salt_hex: str) -> str:
    """Возвращает "enc:<токен>", готовое для записи в config.py как строковый
    литерал. Пустая строка остаётся пустой строкой (нечего шифровать)."""
    if not plaintext:
        return ""
    if not password or not salt_hex:
        # Нет пароля/соли (например, миграция ещё не настроена) — не можем
        # зашифровать, отдаём как есть, чтобы не потерять значение.
        return plaintext
    from cryptography.fernet import Fernet
    key = _derive_fernet_key(password, salt_hex)
    token = Fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")
    return ENC_PREFIX + token


def decrypt_value(stored: str, password: str, salt_hex: str) -> str:
    """Расшифровывает значение с префиксом "enc:". Если значение НЕ
    зашифровано (старый конфиг или пустая строка) — возвращает как есть,
    ничего не ломает."""
    if not stored:
        return stored
    if not isinstance(stored, str) or not stored.startswith(ENC_PREFIX):
        return stored
    from cryptography.fernet import Fernet, InvalidToken
    key = _derive_fernet_key(password, salt_hex)
    token = stored[len(ENC_PREFIX):]
    try:
        return Fernet(key).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, Exception) as e:
        raise ValueError(
            "Не удалось расшифровать секрет в config.py — неверный пароль входа, "
            "либо файл повреждён/подделан."
        ) from e


# Простые строковые поля config.py, которые могут быть зашифрованы.
_SECRET_STR_FIELDS = ("MT5_PASSWORD", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")


def unlock_config(cfg_module, password: str):
    """Расшифровывает секреты config.py ПРЯМО В ПАМЯТИ модуля (на время работы
    процесса) — на диске они остаются зашифрованными. Нужно вызывать один раз
    при входе и заново ПОСЛЕ каждого importlib.reload(cfg) (reload читает файл
    заново с диска — там опять "enc:..." строки).

    Поля, которые уже не зашифрованы (нет префикса "enc:") — просто
    пропускаются, это старый/легаси конфиг или значение только что вписано
    вручную, работает как раньше."""
    salt = getattr(cfg_module, "SECURITY_SALT", "") or ""

    for field in _SECRET_STR_FIELDS:
        raw = getattr(cfg_module, field, "")
        if isinstance(raw, str) and raw.startswith(ENC_PREFIX):
            setattr(cfg_module, field, decrypt_value(raw, password, salt))

    raw_news = getattr(cfg_module, "NEWS_API_KEYS", {})
    if isinstance(raw_news, dict):
        decrypted_news = {}
        for k, v in raw_news.items():
            if isinstance(v, str) and v.startswith(ENC_PREFIX):
                decrypted_news[k] = decrypt_value(v, password, salt)
            else:
                decrypted_news[k] = v
        cfg_module.NEWS_API_KEYS = decrypted_news


def has_encrypted_secrets(cfg_module) -> bool:
    """True, если хоть одно поле в config.py сейчас зашифровано — используется
    CLI-запуском (`python main.py` напрямую), чтобы понять, нужно ли вообще
    спрашивать пароль в консоли (если ничего не зашифровано — не спрашиваем,
    ничего не меняется для тех, кто не пользуется desktop-приложением)."""
    for field in _SECRET_STR_FIELDS:
        raw = getattr(cfg_module, field, "")
        if isinstance(raw, str) and raw.startswith(ENC_PREFIX):
            return True
    raw_news = getattr(cfg_module, "NEWS_API_KEYS", {}) or {}
    if isinstance(raw_news, dict):
        for v in raw_news.values():
            if isinstance(v, str) and v.startswith(ENC_PREFIX):
                return True
    return False


# =====================================================================
# "ЗАПОМНИТЬ ПАРОЛЬ" НА ЭКРАНЕ ВХОДА — Windows DPAPI (без новых pip-пакетов)
# =====================================================================
# DPAPI (CryptProtectData/CryptUnprotectData) шифрует данные ключом, который
# Windows выводит из УЧЁТНОЙ ЗАПИСИ ТЕКУЩЕГО ПОЛЬЗОВАТЕЛЯ на ЭТОМ компьютере.
# Это удобно и осознанно безопасно одновременно: если файл с "запомненным"
# паролем скопировать на другой компьютер (или его найдёт кто-то под другой
# учёткой Windows) — расшифровать его НЕ ПОЛУЧИТСЯ, DPAPI просто откажет.
# То есть "запомнить пароль" работает только там, где было сохранено, и не
# ослабляет защиту при переносе программы на другой ПК (см. секцию про
# шифрование секретов выше — тот же принцип "не привязано, но и не голый текст
# при копировании").
def dpapi_available() -> bool:
    import sys
    return sys.platform == "win32"


def dpapi_protect(data: bytes) -> bytes:
    """Шифрует bytes через Windows DPAPI, привязано к текущему пользователю
    Windows на этом компьютере."""
    if not dpapi_available():
        raise RuntimeError("DPAPI доступен только в Windows")
    import ctypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def dpapi_unprotect(data: bytes) -> bytes:
    """Расшифровывает данные, зашифрованные dpapi_protect() — сработает
    только на ТОМ ЖЕ компьютере/учётной записи Windows, где они были сохранены."""
    if not dpapi_available():
        raise RuntimeError("DPAPI доступен только в Windows")
    import ctypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
