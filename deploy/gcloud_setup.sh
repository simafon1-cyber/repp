#!/usr/bin/env bash
# gcloud_setup.sh — создаёт в Google Cloud машину с Windows, ставит на неё
# MetaTrader 5 и нашу программу, включает автозапуск.
#
# ГДЕ ЗАПУСКАТЬ: в Google Cloud Shell — это чёрное окошко прямо в браузере,
# кнопка со значком «>_» справа сверху в консоли Google Cloud. Там уже есть
# gcloud и уже выполнен вход в ваш аккаунт. Ничего устанавливать и никому
# передавать пароли не нужно.
#
# ЗАПУСК одной строкой:
#
#   curl -sSL https://raw.githubusercontent.com/simafon1-cyber/repp/claude/metatrader5-trading-system-ids42h/deploy/gcloud_setup.sh | bash
#
# Или скачать, посмотреть глазами и запустить:
#
#   curl -sSLO https://raw.githubusercontent.com/simafon1-cyber/repp/claude/metatrader5-trading-system-ids42h/deploy/gcloud_setup.sh
#   less gcloud_setup.sh
#   bash gcloud_setup.sh
#
# ЧТО МОЖНО ПОМЕНЯТЬ, не редактируя файл, — переменные окружения:
#   REGION=europe-west4  ZONE=europe-west4-a  bash gcloud_setup.sh
#   MACHINE=e2-standard-2 bash gcloud_setup.sh
#   MT5_URL="https://ссылка.вашего.брокера/mt5setup.exe" bash gcloud_setup.sh

set -euo pipefail

VM_NAME="${VM_NAME:-ai-scalper}"
ZONE="${ZONE:-europe-west4-a}"        # Нидерланды: ближе к серверам большинства
                                      # европейских брокеров, чем США
MACHINE="${MACHINE:-e2-medium}"       # 2 ядра, 4 ГБ — минимум для терминала
                                      # вместе с программой
DISK_GB="${DISK_GB:-50}"
IMAGE_FAMILY="${IMAGE_FAMILY:-windows-2022}"
BRANCH="${BRANCH:-claude/metatrader5-trading-system-ids42h}"
RAW="https://raw.githubusercontent.com/simafon1-cyber/repp/${BRANCH}/deploy/windows_setup.ps1"

red()  { printf "\033[31m%s\033[0m\n" "$*"; }
bold() { printf "\033[1m%s\033[0m\n" "$*"; }

bold "=== Создание торгового сервера в Google Cloud ==="
echo

# ---------------------------------------------------------------------
# Проверки ДО того, как что-то создавать
# ---------------------------------------------------------------------
if ! command -v gcloud >/dev/null 2>&1; then
    red "gcloud не найден."
    echo "Запускайте этот скрипт в Google Cloud Shell — значок «>_» справа"
    echo "сверху в консоли Google Cloud. Там gcloud уже установлен."
    exit 1
fi

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
if [ -z "$PROJECT" ] || [ "$PROJECT" = "(unset)" ]; then
    red "Не выбран проект Google Cloud."
    echo "Выполните:  gcloud config set project ИМЯ-ПРОЕКТА"
    echo "Имя проекта видно вверху консоли, рядом с логотипом Google Cloud."
    exit 1
fi

echo "Проект:      $PROJECT"
echo "Имя машины:  $VM_NAME"
echo "Зона:        $ZONE"
echo "Размер:      $MACHINE, диск ${DISK_GB} ГБ, $IMAGE_FAMILY"
echo
bold "ВАЖНО ПРО ДЕНЬГИ"
echo "Машина с Windows работает круглосуточно и стоит примерно 45-70 долларов"
echo "в месяц (сама машина + отдельная плата за лицензию Windows + диск)."
echo "Бесплатный уровень Google Cloud сюда НЕ распространяется: там только"
echo "Linux, а лицензия Windows не бесплатна."
echo
echo "Остановить машину и перестать платить за неё можно так:"
echo "  gcloud compute instances stop $VM_NAME --zone $ZONE"
echo "Удалить совсем:"
echo "  gcloud compute instances delete $VM_NAME --zone $ZONE"
echo
read -r -p "Продолжаем? Напишите да и нажмите Enter: " answer
case "$answer" in
    да|ДА|Да|yes|y|Y) ;;
    *) echo "Отменено, ничего не создано."; exit 0 ;;
esac

# Compute Engine может быть ещё не включён в новом проекте
echo
echo "Проверяю, включён ли Compute Engine..."
gcloud services enable compute.googleapis.com --project "$PROJECT" --quiet

# Машина с таким именем уже может существовать
if gcloud compute instances describe "$VM_NAME" --zone "$ZONE" \
        --project "$PROJECT" >/dev/null 2>&1; then
    red "Машина «$VM_NAME» в зоне $ZONE уже существует."
    echo "Удалите её или задайте другое имя:  VM_NAME=другое-имя bash $0"
    exit 1
fi

# ---------------------------------------------------------------------
# Создание
# ---------------------------------------------------------------------
echo
echo "Создаю машину. Это занимает 1-2 минуты..."

# Скрипт установки берётся по ссылке, а не вставляется сюда текстом: так его
# видно целиком в репозитории и можно прочитать до запуска.
gcloud compute instances create "$VM_NAME" \
    --project="$PROJECT" \
    --zone="$ZONE" \
    --machine-type="$MACHINE" \
    --image-family="$IMAGE_FAMILY" \
    --image-project=windows-cloud \
    --boot-disk-size="${DISK_GB}GB" \
    --boot-disk-type=pd-balanced \
    --metadata="windows-startup-script-url=$RAW" \
    --tags=ai-scalper \
    --quiet

echo
bold "Машина создана."

IP=$(gcloud compute instances describe "$VM_NAME" --zone "$ZONE" \
        --project "$PROJECT" \
        --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

cat <<KONEC

================================================================
ЧТО ДАЛЬШЕ — три шага
================================================================

1) ЗАДАТЬ ПАРОЛЬ WINDOWS и войти

   gcloud compute reset-windows-password $VM_NAME --zone $ZONE

   Команда покажет логин и пароль — запишите их.
   Затем на своём компьютере откройте «Подключение к удалённому
   рабочему столу» (Win+R -> mstsc) и введите адрес:

       $IP

2) ПОДОЖДАТЬ УСТАНОВКУ

   MetaTrader 5 и наша программа ставятся САМИ при первом включении
   машины. Это занимает 5-10 минут после создания.

   Если зашли раньше и ярлыков на рабочем столе ещё нет — подождите
   и обновите рабочий стол. Ход установки видно в файле:

       C:\\AIScalper\\установка.log

3) ВОЙТИ В СЧЁТ И ЗАДАТЬ ПАРОЛЬ ДАШБОРДА

   Внутри Windows откройте ярлык MetaTrader 5, войдите в свой счёт,
   включите «Разрешить автоматическую торговлю».
   Затем откройте AI Scalper -> вкладка «Система» -> задайте пароль
   дашборда. Без пароля дашборд не работает, это защита.

   Подробности лежат на рабочем столе в файле «ПРОЧТИ МЕНЯ.txt».

================================================================
ЗАПОМНИТЕ ОДНО ПРАВИЛО
================================================================
Закрывая удалённый рабочий стол, нажимайте «Отключиться»,
а НЕ «Выйти из системы». Выход закрывает сеанс Windows вместе с
терминалом — торговля остановится.

================================================================
ЕСЛИ ЗАХОТИТЕ ОТКРЫТЬ ДАШБОРД С ТЕЛЕФОНА
================================================================
Порт 5000 закрыт намеренно. Открывать его всему интернету НЕЛЬЗЯ:
через дашборд можно останавливать и запускать торговлю, а пароль
идёт по обычному HTTP и по дороге виден.

Открыть только со своего домашнего адреса:

   МОЙ_IP=\$(curl -s ifconfig.me)
   gcloud compute firewall-rules create ai-scalper-dashboard \\
       --allow=tcp:5000 --target-tags=ai-scalper \\
       --source-ranges="\$МОЙ_IP/32" --project=$PROJECT

Домашний адрес у большинства провайдеров меняется — если дашборд
перестанет открываться, правило нужно будет обновить.

================================================================
СКОЛЬКО ЭТО СТОИТ И КАК ВЫКЛЮЧИТЬ
================================================================
Остановить (перестать платить за машину):
   gcloud compute instances stop $VM_NAME --zone $ZONE
Включить обратно:
   gcloud compute instances start $VM_NAME --zone $ZONE
Удалить совсем:
   gcloud compute instances delete $VM_NAME --zone $ZONE

KONEC
