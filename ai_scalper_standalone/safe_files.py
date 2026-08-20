"""
safe_files.py — более безопасная и надёжная работа с файлами настроек/логов:

  1) Атомарная запись (временный файл + os.replace) — исключает "порванный"
     config.py, если запись прервётся посреди (антивирус, выключение света,
     сбой диска). Оригинал либо остаётся старым целиком, либо становится
     новым целиком — промежуточного повреждённого состояния не бывает.
  2) Ротация резервных копий (config.py.bak1 .. bak5) перед каждой
     перезаписью — можно вручную откатиться, если что-то вписали неправильно.
  3) Проверка синтаксиса ПЕРЕД заменой оригинала (для .py-файлов) — если
     новое содержимое не компилируется, запись отменяется и config.py не
     трогается вообще.
  4) SHA-256 сайдкар-файл для проверки целостности — обнаруживает, что файл
     (например, trades_log.csv — журнал сделок) изменился СНАРУЖИ программы
     между запусками, что может говорить о постороннем вмешательстве.
  5) Ограничение доступа к файлу через Windows ACL — только текущий
     пользователь Windows может читать/писать файл, а не все локальные
     учётки на этом компьютере.

Всё — best-effort: если какой-то шаг не удался (сетевой диск, FAT32, нет
прав на icacls и т.д.) — соответствующая функция просто логирует
предупреждение и не мешает остальной программе работать. Это ДОПОЛНИТЕЛЬНАЯ
защита, а не критичная зависимость — торговый цикл не должен падать из-за неё.
"""

import hashlib
import logging
import os
import shutil
import sys
import tempfile

log = logging.getLogger("safe_files")

MAX_BACKUPS = 5
_INTEGRITY_SUFFIX = ".sha256"


def sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _backup_path(path: str, index: int) -> str:
    return f"{path}.bak{index}"


def _rotate_backups(path: str):
    """config.py.bak1 — самая свежая копия ПЕРЕД текущей записью, .bak2 —
    старее, и т.д. до MAX_BACKUPS; самая старая копия удаляется."""
    if not os.path.exists(path):
        return
    try:
        oldest = _backup_path(path, MAX_BACKUPS)
        if os.path.exists(oldest):
            os.remove(oldest)
        for i in range(MAX_BACKUPS - 1, 0, -1):
            src = _backup_path(path, i)
            if os.path.exists(src):
                os.replace(src, _backup_path(path, i + 1))
        newest_backup = _backup_path(path, 1)
        shutil.copy2(path, newest_backup)
        restrict_to_current_user(newest_backup)
    except OSError as e:
        log.warning("Не удалось сделать резервную копию %s: %s", path, e)


def validate_python_syntax(content: str):
    """Бросает исключение, если content не валидный Python — используется как
    validate= в atomic_write_text() для config.py, чтобы никогда не записать
    синтаксически битый файл."""
    compile(content, "<validation>", "exec")


def atomic_write_text(path: str, content: str, encoding: str = "utf-8",
                       backup: bool = True, validate=None) -> None:
    """Безопасная перезапись текстового файла целиком:
      1. (опционально) проверяет content функцией validate(content) — бросает
         исключение сама, если что-то не так, тогда запись отменяется;
      2. пишет во временный файл РЯДОМ с оригиналом (та же файловая система —
         важно для атомарности os.replace);
      3. (опционально) делает резервную копию оригинала;
      4. заменяет оригинал одной атомарной ОС-операцией (os.replace) — не
         бывает состояния "файл наполовину записан".
    """
    if validate is not None:
        validate(content)

    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        if backup:
            _rotate_backups(path)

        os.replace(tmp_path, path)  # атомарно и на Windows, и на POSIX
        _fsync_directory(directory)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise

    _update_integrity_sidecar(path)


def _fsync_directory(directory: str):
    """Сбросить на диск САМУ ЗАПИСЬ О ФАЙЛЕ в каталоге.

    os.replace атомарен, но атомарность и долговечность — разные вещи.
    После замены новое имя файла может ещё лежать в кэше каталога: при
    внезапном отключении питания система вернётся к состоянию, где нового
    файла нет. Для отметки об остановке (incident.json) это значило бы,
    что запрет исчез именно после того сбоя, из-за которого он и появился.

    На Windows каталог как файл не открывается, и такого вызова там нет —
    молча пропускаем: это дополнительная защита, а не обязательная."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(fd)
    except OSError as e:
        log.debug("fsync каталога %s не удался: %s", directory, e)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def append_line_safely(path: str, write_fn, encoding: str = "utf-8"):
    """Дозапись в конец файла (журнал сделок и т.п.) с flush+fsync сразу после
    записи — данные гарантированно попадают на диск, а не теряются в кэше ОС
    при внезапном завершении процесса. write_fn(file_obj) сама пишет нужные
    строки через переданный файловый объект."""
    with open(path, "a", newline="", encoding=encoding) as f:
        write_fn(f)
        f.flush()
        os.fsync(f.fileno())
    _update_integrity_sidecar(path)


def _update_integrity_sidecar(path: str):
    try:
        digest = sha256_of_file(path)
        if not digest:
            return
        with open(path + _INTEGRITY_SUFFIX, "w", encoding="utf-8") as f:
            f.write(digest)
    except OSError:
        pass


def mark_integrity_current(path: str):
    """Явно обновляет сайдкар-хэш — используй после операций, которые не идут
    через atomic_write_text/append_line_safely (например, создание файла с
    нуля где-то ещё), чтобы следующая check_integrity() не выдала ложную
    тревогу."""
    _update_integrity_sidecar(path)


def check_integrity(path: str) -> bool:
    """True — файл совпадает с сайдкар-хэшем от последней записи ЧЕРЕЗ ЭТОТ
    модуль (или сайдкара ещё нет — тогда считаем, что всё в порядке, и просто
    создаём его). False — файл изменился СНАРУЖИ программы с прошлого раза:
    не обязательно взлом (может быть, файл случайно тронули руками), но повод
    насторожиться, особенно для журнала сделок."""
    if not os.path.exists(path):
        return True
    sidecar = path + _INTEGRITY_SUFFIX
    if not os.path.exists(sidecar):
        _update_integrity_sidecar(path)
        return True
    try:
        with open(sidecar, encoding="utf-8") as f:
            expected = f.read().strip()
    except OSError:
        return True
    actual = sha256_of_file(path)
    if not actual or not expected:
        return True
    return actual == expected


def restrict_to_current_user(path: str):
    """Windows ACL: доступ к файлу только у текущего пользователя Windows —
    остальные локальные учётки этого компьютера доступа не получают (важно,
    если компьютер общий или программу перенесли на чужую машину и забыли
    сменить пользователя). Best-effort: если файл на сетевом диске/FAT32 без
    поддержки ACL, или icacls недоступен — молча пропускаем, это не критично
    для работы программы."""
    if sys.platform != "win32":
        return
    if not os.path.exists(path):
        return
    try:
        import subprocess
        username = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        if not username:
            return
        domain_user = username if "\\" in username else f"{os.environ.get('USERDOMAIN', '')}\\{username}".lstrip("\\")
        subprocess.run(
            ["icacls", path, "/inheritance:r", "/grant:r", f"{domain_user}:F"],
            capture_output=True, timeout=5, check=False,
        )
    except Exception as e:
        log.debug("Не удалось ограничить доступ к файлу %s: %s", path, e)
