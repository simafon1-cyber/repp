"""Шифрование паролей торговых счетов средствами Windows (DPAPI).

DPAPI привязывает шифрование к учётной записи Windows: если файл со счетами
скопируют на другой компьютер или откроют под другим пользователем, пароли
не расшифруются. Вводить мастер-пароль при этом не нужно, поэтому программа
может запускаться автоматически.

Реализовано через ctypes, дополнительных пакетов не требуется.

На не-Windows системах (нужно для автоматических тестов) используется
запасной режим: данные кодируются обратимо и ЯВНО помечаются как
незашифрованные, чтобы это нельзя было принять за защиту.
"""

from __future__ import annotations

import base64
import ctypes
import platform
from ctypes import wintypes

IS_WINDOWS = platform.system() == "Windows"

# Метки формата: по ним видно, чем зашифрована строка
PREFIX_DPAPI = "dpapi:"
PREFIX_PLAIN = "plain:"

# Дополнительная привязка: расшифровать смогут только с этой же меткой
ENTROPY = b"TraderApp.MT5.accounts.v1"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DataBlob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _blob_bytes(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _free(blob: _DataBlob) -> None:
    if blob.pbData:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def _dpapi_encrypt(raw: bytes) -> bytes:
    data_in = _blob(raw)
    entropy = _blob(ENTROPY)
    data_out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(data_in), None, ctypes.byref(entropy), None, None, 0,
        ctypes.byref(data_out),
    )
    if not ok:
        raise OSError("CryptProtectData не смог зашифровать данные")
    try:
        return _blob_bytes(data_out)
    finally:
        _free(data_out)


def _dpapi_decrypt(raw: bytes) -> bytes:
    data_in = _blob(raw)
    entropy = _blob(ENTROPY)
    data_out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(data_in), None, ctypes.byref(entropy), None, None, 0,
        ctypes.byref(data_out),
    )
    if not ok:
        raise OSError(
            "CryptUnprotectData не смог расшифровать данные. "
            "Обычно это значит, что файл создан на другом компьютере "
            "или под другой учётной записью Windows."
        )
    try:
        return _blob_bytes(data_out)
    finally:
        _free(data_out)


def encrypt(text: str) -> str:
    """Шифрует строку. Возвращает строку с меткой формата."""
    if text == "":
        return ""
    raw = text.encode("utf-8")
    if IS_WINDOWS:
        return PREFIX_DPAPI + base64.b64encode(_dpapi_encrypt(raw)).decode("ascii")
    # Запасной режим — НЕ защита, только чтобы код работал вне Windows
    return PREFIX_PLAIN + base64.b64encode(raw).decode("ascii")


def decrypt(stored: str) -> str:
    """Расшифровывает строку, созданную encrypt()."""
    if not stored:
        return ""
    if stored.startswith(PREFIX_DPAPI):
        payload = base64.b64decode(stored[len(PREFIX_DPAPI):])
        if not IS_WINDOWS:
            raise OSError(
                "Пароли зашифрованы средствами Windows и на этой системе "
                "не расшифровываются."
            )
        return _dpapi_decrypt(payload).decode("utf-8")
    if stored.startswith(PREFIX_PLAIN):
        return base64.b64decode(stored[len(PREFIX_PLAIN):]).decode("utf-8")
    # Файл от старой версии, где пароль лежал открытым текстом
    return stored


def is_protected(stored: str) -> bool:
    """True, если строка действительно зашифрована средствами Windows."""
    return stored.startswith(PREFIX_DPAPI)


def storage_status() -> str:
    """Понятное описание защиты — показывается в интерфейсе."""
    if IS_WINDOWS:
        return "Пароли зашифрованы средствами Windows (DPAPI), привязаны к вашей учётной записи"
    return "ВНИМАНИЕ: не Windows — пароли НЕ зашифрованы (запасной режим для тестов)"
