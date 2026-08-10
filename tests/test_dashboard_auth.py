#!/usr/bin/env python3
"""Тесты защиты веб-дашборда.

ОТКУДА ЗАДАЧА. Владелец спросил, можно ли перенести программу на Google Cloud,
чтобы она работала удалённо. Перед этим нужно закрыть дыру, которая дома была
терпимой, а на публичном сервере стала бы опасной.

Дашборд слушает на ВСЕХ адресах (0.0.0.0), а пароль по умолчанию пуст. Старая
проверка сводилась к `password == ""`, то есть любой, кто прислал правильный
логин с пустым паролем, попадал внутрь. Логин — это адрес почты владельца, он
лежит в config.py и секретом не является. А через дашборд можно останавливать
и запускать торговлю, менять инструменты.

Теперь: пароля нет — дашборда нет.

Запуск:  python3 tests/test_dashboard_auth.py
"""

from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
APP = ROOT / "ai_scalper_standalone"
sys.path.insert(0, str(APP))

passed = 0
failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  OK   {name}")
    else:
        failed += 1
        print(f"  СБОЙ {name}" + (f"  -> {detail}" if detail else ""))


cfg = types.ModuleType("config")
cfg.__file__ = str(APP / "config.py.example")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg
CFG = cfg


class _FakeMT5(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return name


sys.modules["MetaTrader5"] = _FakeMT5("MetaTrader5")


# Flask на этой машине не ставится — подменяем ровно тем, что использует
# web_dashboard. Проверяем при этом НАСТОЯЩИЙ код проверки пароля, а не его
# пересказ.
class _FakeResponse:
    def __init__(self, body="", status=200, headers=None):
        self.body = body
        self.status = status
        self.headers = headers or {}


class _FakeApp:
    def __init__(self, *a, **k):
        self.before = []

    def before_request(self, fn):
        self.before.append(fn)
        return fn

    def route(self, *a, **k):
        return lambda fn: fn

    def run(self, *a, **k):
        raise AssertionError("сервер в тесте запускаться не должен")


flask = types.ModuleType("flask")
flask.Flask = _FakeApp
flask.jsonify = lambda *a, **k: _FakeResponse()
flask.request = types.SimpleNamespace(authorization=None)
flask.Response = lambda body="", status=200, headers=None: _FakeResponse(body, status, headers)
sys.modules["flask"] = flask

import web_dashboard as wd     # noqa: E402


def _set(password="", pw_hash=""):
    CFG.DASHBOARD_PASSWORD = password
    CFG.DASHBOARD_PASSWORD_HASH = pw_hash


def test_empty_password_is_not_a_password() -> None:
    """ГЛАВНОЕ. Пустой пароль раньше работал как настоящий."""
    print("\n[Пустой пароль не пускает никого]")
    saved = (CFG.DASHBOARD_PASSWORD, CFG.DASHBOARD_PASSWORD_HASH)
    try:
        _set(password="", pw_hash="")
        check(wd.password_is_set() is False, "Пароль считается незаданным")
        check(wd._check_auth(CFG.DASHBOARD_LOGIN, "") is False,
              "Правильный логин с пустым паролем НЕ пускает")
        check(wd._check_auth(CFG.DASHBOARD_LOGIN, "что угодно") is False,
              "И с любым другим паролем тоже")

        # Ответ должен объяснять, что пароль не задан, а не «неверный пароль»:
        # иначе человек будет искать забытый пароль вместо того, чтобы его
        # завести.
        resp = wd._no_password_response()
        check(resp.status == 403, "Отдаётся 403, а не приглашение ввести пароль",
              str(resp.status))
        check("не задан" in resp.body, "Сказано, что пароль не задан", resp.body[:60])
        check("Система" in resp.body, "И где его задать", resp.body[:200])
    finally:
        CFG.DASHBOARD_PASSWORD, CFG.DASHBOARD_PASSWORD_HASH = saved


def test_real_password_still_works() -> None:
    print("\n[С заданным паролем всё работает как раньше]")
    saved = (CFG.DASHBOARD_PASSWORD, CFG.DASHBOARD_PASSWORD_HASH)
    try:
        _set(password="секрет123", pw_hash="")
        check(wd.password_is_set() is True, "Пароль задан")
        check(wd._check_auth(CFG.DASHBOARD_LOGIN, "секрет123") is True,
              "Верный логин и пароль пускают")
        check(wd._check_auth(CFG.DASHBOARD_LOGIN, "не тот") is False,
              "Неверный пароль не пускает")
        check(wd._check_auth("чужой@почта", "секрет123") is False,
              "Чужой логин не пускает")
    finally:
        CFG.DASHBOARD_PASSWORD, CFG.DASHBOARD_PASSWORD_HASH = saved


def test_hashed_password_works() -> None:
    """Новый формат: пароль хранится только хэшем."""
    print("\n[Пароль, сохранённый хэшем]")
    import secure_store
    saved = (CFG.DASHBOARD_PASSWORD, CFG.DASHBOARD_PASSWORD_HASH,
             getattr(CFG, "SECURITY_SALT", ""))
    try:
        salt = secure_store.new_salt()
        CFG.SECURITY_SALT = salt
        _set(password="", pw_hash=secure_store.hash_password("мойпароль", salt))
        check(wd.password_is_set() is True,
              "Хэш считается заданным паролем, даже когда открытый текст пуст")
        check(wd._check_auth(CFG.DASHBOARD_LOGIN, "мойпароль") is True,
              "Верный пароль проходит по хэшу")
        check(wd._check_auth(CFG.DASHBOARD_LOGIN, "другой") is False,
              "Неверный — нет")
        check(wd._check_auth(CFG.DASHBOARD_LOGIN, "") is False,
              "И пустой при заданном хэше тоже нет")
    finally:
        (CFG.DASHBOARD_PASSWORD, CFG.DASHBOARD_PASSWORD_HASH,
         CFG.SECURITY_SALT) = saved


def test_guard_runs_before_every_request() -> None:
    """Проверка обязана стоять на КАЖДОМ запросе, а не на отдельных страницах:
    иначе одна забытая страница откроет управление торговлей."""
    print("\n[Проверка стоит на каждом запросе]")
    src = (APP / "web_dashboard.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    guarded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            name = getattr(dec, "attr", "") or getattr(dec, "id", "")
            if name == "before_request":
                guarded = True
                body = ast.dump(node)
                check("password_is_set" in body,
                      "И первым делом смотрит, задан ли пароль вообще")
    check(guarded, "Обработчик before_request существует")


def test_config_warns_about_public_access() -> None:
    print("\n[В настройках предупреждено про доступ снаружи]")
    example = (APP / "config.py.example").read_text(encoding="utf-8")
    block = example.split("DASHBOARD_PORT", 1)[0][-1500:] + \
        example.split("DASHBOARD_PORT", 1)[1][:1500]
    check("пароль" in block.lower(),
          "Про пароль сказано рядом с настройками дашборда")


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ: ЗАЩИТА ВЕБ-ДАШБОРДА")
    print("=" * 62)

    test_empty_password_is_not_a_password()
    test_real_password_still_works()
    test_hashed_password_works()
    test_guard_runs_before_every_request()
    test_config_warns_about_public_access()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
