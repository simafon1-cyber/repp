#!/usr/bin/env python3
"""Тесты скриптов установки на сервер.

ЗАЧЕМ. Эти скрипты человек запускает ОДИН раз на чистой машине, и ошибку в
них он не отладит: он не программист, а машина в облаке. Значит проверять их
надо здесь, а не на его сервере.

PowerShell на машине сборки нет, поэтому проверяется не выполнение, а то, из
чего ошибка обычно и получается: сбалансированность блоков, наличие обработки
неудачной загрузки, отсутствие тихих провалов, и что ссылки ведут туда, куда
задумано.

Запуск:  python3 tests/test_deploy_scripts.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
DEPLOY = ROOT / "deploy"

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


SH = (DEPLOY / "gcloud_setup.sh").read_text(encoding="utf-8")
PS = (DEPLOY / "windows_setup.ps1").read_text(encoding="utf-8")


def test_files_exist() -> None:
    print("\n[Скрипты на месте]")
    check((DEPLOY / "gcloud_setup.sh").exists(), "Скрипт создания машины есть")
    check((DEPLOY / "windows_setup.ps1").exists(), "Скрипт установки внутри Windows есть")


def test_bash_syntax() -> None:
    print("\n[Синтаксис скрипта Google Cloud]")
    r = subprocess.run(["bash", "-n", str(DEPLOY / "gcloud_setup.sh")],
                       capture_output=True, text=True)
    check(r.returncode == 0, "bash -n проходит", r.stderr[:200])
    check(SH.startswith("#!/usr/bin/env bash"), "Есть строка запуска")
    check("set -euo pipefail" in SH,
          "Скрипт останавливается на первой же ошибке, а не идёт дальше вслепую")


def test_powershell_blocks_balanced() -> None:
    """Незакрытая скобка в PowerShell — самая частая и самая незаметная
    ошибка: скрипт запускается и молча делает не то."""
    print("\n[Скобки в PowerShell сходятся]")
    body = re.sub(r'@"[\s\S]*?"@', '""', PS)     # here-string целиком
    body = re.sub(r'"(?:[^"`]|`.)*"', '""', body)
    body = re.sub(r"'[^']*'", "''", body)
    body = re.sub(r"(?m)^\s*#.*$", "", body)
    for op, cl, name in (("{", "}", "фигурные"), ("(", ")", "круглые")):
        check(body.count(op) == body.count(cl), f"{name} скобки сходятся",
              f"{body.count(op)} против {body.count(cl)}")


def test_download_failures_are_loud() -> None:
    """Не скачался файл — человек обязан это увидеть. Тихий провал оставит
    его с пустой машиной и без единой подсказки."""
    print("\n[Неудачная загрузка не проходит молча]")
    check("try {" in PS and "catch {" in PS, "Загрузка обёрнута в обработку ошибок")
    # Проверять «ОШИБКА есть где-то в файле» бесполезно: это слово встречается
    # и в других местах, и проверка проходила бы даже после удаления сообщения
    # из самой загрузки. Смотрим ИМЕННО тело функции Download.
    dl = PS.split("function Download", 1)[1].split("\n}", 1)[0]
    check("ОШИБКА" in dl,
          "При неудачной загрузке пишется слово ОШИБКА — в самой загрузке, "
          "а не где-то ещё в файле")
    check("return $false" in dl, "Функция загрузки честно возвращает неудачу")
    check("$_.Exception.Message" in dl, "И называет причину, а не просто «не вышло»")
    check("Add-Content" in PS, "Всё пишется в журнал на диске")


def test_install_is_repeatable() -> None:
    """Google Cloud выполняет скрипт при КАЖДОЙ перезагрузке. Повторный
    запуск не должен ломать уже работающую машину."""
    print("\n[Повторный запуск безопасен]")
    check("уже стоит" in PS or "уже скачан" in PS,
          "Готовые шаги пропускаются")
    # Проверяем ИМЕННО тело загрузки: «Test-Path есть где-то в файле» проходило
    # бы и после того, как из загрузки его убрали, — а тогда при каждой
    # перезагрузке машина заново качала бы 60 МБ.
    dl = PS.split("function Download", 1)[1].split("\n}", 1)[0]
    check("Test-Path $target" in dl,
          "Загрузка пропускает уже скачанный файл")
    check("предыдущая" in PS,
          "При обновлении программы старая версия сохраняется — будет к чему "
          "вернуться, если новая не запустится")


def test_autostart_and_session_rules() -> None:
    print("\n[Автозапуск и правило про выход из системы]")
    # Опять же по месту, а не по всему файлу: переименуй команду — и проверка
    # по подстроке «Run» этого не заметила бы.
    auto = PS.split("# 3. Автозапуск", 1)[1].split("# 4. Ярлыки", 1)[0]
    # Комментарии выбрасываем: в этом разделе объяснение начинается словами
    # «HKLM, а не HKCU», и проверка по подстроке проходила бы даже после
    # замены самого ключа на HKCU.
    code = "\n".join(ln for ln in auto.splitlines()
                     if not ln.strip().startswith("#"))
    check(len(code) > 200, "Раздел автозапуска найден целиком "
                           "(иначе проверки ниже смотрели бы в пустоту)",
          f"{len(code)} знаков")
    check("CurrentVersion\\Run" in code, "Автозапуск прописывается в реестр")
    # Точное имя команды, а не подстрока: New-ItemPropertyX содержит в себе
    # New-ItemProperty, и обычная проверка приняла бы опечатку за правильный
    # вызов.
    check(re.search(r"\bNew-ItemProperty\b(?!\w)", code) is not None,
          "И именно командой записи в реестр, а не похожей по названию",
          code[:120])
    check(re.search(r'\$runKey\s*=\s*"HKLM:', code) is not None,
          "Ключ именно в HKLM: скрипт работает от SYSTEM, профиля "
          "пользователя может ещё не быть", code[:200])
    check("Отключиться" in PS and "Выйти из системы" in PS,
          "Человека предупреждают про разницу между «Отключиться» и «Выйти»")
    check("ПРОЧТИ МЕНЯ" in PS, "Памятка кладётся на рабочий стол")


def test_dashboard_port_is_not_opened() -> None:
    """САМОЕ ВАЖНОЕ ПО БЕЗОПАСНОСТИ. Через дашборд можно останавливать и
    запускать торговлю. Скрипт НЕ должен открывать порт сам."""
    print("\n[Порт дашборда не открывается автоматически]")
    # Ищем ВЫПОЛНЯЕМУЮ команду, а не строку из текста подсказки: скрипт
    # печатает пример правила через heredoc, и поиск по всему файлу принял бы
    # объяснение за само действие. (На это моя первая версия проверки и
    # попалась.)
    executable = re.sub(r"cat <<'?KONEC'?[\s\S]*?\nKONEC", "", SH)
    check("firewall-rules create" in SH,
          "Пример правила в подсказке есть (иначе проверка ниже пуста)")
    creates_rule = re.search(r"^\s*gcloud compute firewall-rules create",
                             executable, re.MULTILINE)
    check(creates_rule is None,
          "Скрипт не создаёт правило брандмауэра сам",
          creates_rule.group(0) if creates_rule else "")
    check("0.0.0.0/0" not in SH,
          "И нигде не предлагает открыть порт всему интернету")
    check("source-ranges" in SH and "/32" in SH,
          "В подсказке — только свой адрес")
    check("по дороге виден" in SH,
          "Объяснено, почему нельзя открывать порт всем")


def test_money_warning_before_creating() -> None:
    """Машина с Windows стоит 45-70 долларов в месяц. Человек должен узнать
    это ДО создания, а не из счёта."""
    print("\n[Про деньги сказано до создания машины]")
    before = SH.split("gcloud compute instances create", 1)[0]
    check("45-70" in before, "Цена названа до создания")
    check("Бесплатный уровень" in before,
          "И сказано, что бесплатный уровень сюда не распространяется")
    check("read -r -p" in before, "Скрипт ждёт подтверждения")
    check("instances stop" in before,
          "Показано, как остановить и перестать платить")


def test_urls_point_where_intended() -> None:
    print("\n[Ссылки ведут куда задумано]")
    check("releases/latest/download/AI_Scalper_Pro.exe" in PS,
          "Программа берётся из последнего выпуска")
    check("windows-startup-script-url" in SH,
          "Google Cloud получает ссылку на скрипт установки")
    check("deploy/windows_setup.ps1" in SH,
          "И это именно наш скрипт")
    # Ветка должна совпадать в обоих местах, иначе скачается не тот файл
    branch_sh = re.search(r'BRANCH:-([^\}"]+)', SH)
    check(branch_sh is not None, "Ветка задана переменной")
    if branch_sh:
        check(branch_sh.group(1) in SH.split("RAW=", 1)[1][:200] or
              "${BRANCH}" in SH.split("RAW=", 1)[1][:200],
              "Ссылка на скрипт собирается из этой же ветки")


def test_preflight_checks() -> None:
    """Проверки ДО создания — чтобы человек не получил половину настроенного
    сервера и невнятную ошибку."""
    print("\n[Проверки до создания машины]")
    before = SH.split("gcloud compute instances create", 1)[0]
    check("command -v gcloud" in before, "Проверяется наличие gcloud")
    check("Cloud Shell" in before, "И подсказано, где его взять")
    check("config get-value project" in before, "Проверяется выбранный проект")
    check("services enable compute" in before,
          "Compute Engine включается сам — в новом проекте он выключен")
    check("instances describe" in before,
          "Проверяется, что машина с таким именем ещё не создана")


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ: СКРИПТЫ УСТАНОВКИ НА СЕРВЕР")
    print("=" * 62)

    test_files_exist()
    test_bash_syntax()
    test_powershell_blocks_balanced()
    test_download_failures_are_loud()
    test_install_is_repeatable()
    test_autostart_and_session_rules()
    test_dashboard_port_is_not_opened()
    test_money_warning_before_creating()
    test_urls_point_where_intended()
    test_preflight_checks()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
