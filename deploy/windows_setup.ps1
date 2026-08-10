# windows_setup.ps1 — установка терминала и программы на чистую Windows-машину.
#
# Google Cloud запускает этот скрипт САМ при каждом включении машины (metadata
# windows-startup-script-ps1). Человеку заходить внутрь для установки не нужно.
#
# Скрипт запускается от имени SYSTEM ещё ДО того, как кто-либо вошёл в
# Windows. Поэтому он только СТАВИТ программы, но не запускает торговлю:
# терминалу MetaTrader нужен живой сеанс пользователя. Автозапуск прописан в
# реестре и сработает, когда вы войдёте по удалённому рабочему столу.
#
# Скрипт можно запускать сколько угодно раз: всё, что уже сделано,
# пропускается. Google Cloud выполняет его при КАЖДОЙ перезагрузке.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # без него загрузка в 60 МБ тормозит

$Root    = "C:\AIScalper"
$LogFile = "$Root\установка.log"
$Marker  = "$Root\установлено.txt"

function Say($text) {
    $line = "{0}  {1}" -f (Get-Date -Format "dd.MM HH:mm:ss"), $text
    Write-Host $line
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch { }
}

New-Item -ItemType Directory -Force -Path $Root | Out-Null
Say "=== Запуск установки ==="

# TLS 1.2: без него старые сборки Windows Server не могут скачать по https и
# падают с невнятной ошибкой сертификата.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Get-Meta($key) {
    try {
        Invoke-RestMethod -Headers @{"Metadata-Flavor" = "Google"} `
            -Uri "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$key" `
            -TimeoutSec 10
    } catch { "" }
}

function Download($url, $target, $what) {
    if (Test-Path $target) {
        Say "$what уже скачан, пропускаю"
        return $true
    }
    Say "Скачиваю $what ..."
    try {
        Invoke-WebRequest -Uri $url -OutFile $target -UseBasicParsing -TimeoutSec 900
    } catch {
        # Молчать нельзя: без файла ничего не заработает, а человек увидит
        # пустую машину и не поймёт, что случилось.
        Say "ОШИБКА: не удалось скачать $what с $url"
        Say "Причина: $($_.Exception.Message)"
        return $false
    }
    $size = [math]::Round((Get-Item $target).Length / 1MB, 1)
    Say "$what скачан, $size МБ"
    return $true
}

# ---------------------------------------------------------------------
# 1. MetaTrader 5
# ---------------------------------------------------------------------
$mtPaths = @(
    "C:\Program Files\MetaTrader 5\terminal64.exe",
    "C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"
)
$mtInstalled = $mtPaths | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($mtInstalled) {
    Say "MetaTrader 5 уже стоит: $mtInstalled"
} else {
    # Ссылку можно подменить на установщик СВОЕГО брокера — он приносит с
    # собой уже прописанный сервер, и в окне входа не надо искать его руками.
    $mtUrl = Get-Meta "mt5-installer-url"
    if (-not $mtUrl) {
        $mtUrl = "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"
    }
    $setup = "$Root\mt5setup.exe"
    if (Download $mtUrl $setup "установщик MetaTrader 5") {
        Say "Ставлю MetaTrader 5 (тихая установка, до 5 минут)..."
        try {
            # /auto — штатный тихий режим установщика MetaQuotes
            Start-Process -FilePath $setup -ArgumentList "/auto" -Wait -PassThru | Out-Null
            Start-Sleep -Seconds 20
            $mtInstalled = $mtPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
            if ($mtInstalled) { Say "MetaTrader 5 установлен: $mtInstalled" }
            else { Say "ВНИМАНИЕ: установщик отработал, но terminal64.exe не найден. Поставьте терминал вручную." }
        } catch {
            Say "ОШИБКА установки MetaTrader: $($_.Exception.Message)"
        }
    }
}

# ---------------------------------------------------------------------
# 2. Наша программа
# ---------------------------------------------------------------------
$exe = "$Root\AI_Scalper_Pro.exe"
$appUrl = Get-Meta "app-url"
if (-not $appUrl) {
    $appUrl = "https://github.com/simafon1-cyber/repp/releases/latest/download/AI_Scalper_Pro.exe"
}

# Программу обновляем при каждой перезагрузке: released-сборка меняется часто,
# а машина всё равно перезапускается редко. Старый файл сохраняем — если новая
# сборка вдруг не запустится, будет к чему вернуться.
if (Test-Path $exe) {
    Say "Обновляю программу (старая версия останется как .предыдущая)"
    $fresh = "$Root\AI_Scalper_Pro.new"
    if (Download $appUrl $fresh "новая сборка программы") {
        Move-Item -Force $exe "$Root\AI_Scalper_Pro.предыдущая" -ErrorAction SilentlyContinue
        Move-Item -Force $fresh $exe
    }
} else {
    Download $appUrl $exe "программа AI Scalper" | Out-Null
}

# ---------------------------------------------------------------------
# 3. Автозапуск при входе в Windows
# ---------------------------------------------------------------------
# HKLM, а не HKCU: скрипт работает от SYSTEM, и профиля пользователя ещё
# может не существовать. Запись в HKLM сработает для любого, кто войдёт.
if (Test-Path $exe) {
    try {
        $runKey = "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run"
        New-ItemProperty -Path $runKey -Name "AIScalperPro" -Value "`"$exe`"" `
            -PropertyType String -Force | Out-Null
        Say "Автозапуск прописан"
    } catch {
        Say "ОШИБКА автозапуска: $($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------------
# 4. Ярлыки на рабочем столе — чтобы не искать файлы по папкам
# ---------------------------------------------------------------------
try {
    $desktop = "C:\Users\Public\Desktop"
    $shell = New-Object -ComObject WScript.Shell
    if (Test-Path $exe) {
        $lnk = $shell.CreateShortcut("$desktop\AI Scalper.lnk")
        $lnk.TargetPath = $exe
        $lnk.WorkingDirectory = $Root
        $lnk.Save()
    }
    if ($mtInstalled) {
        $lnk2 = $shell.CreateShortcut("$desktop\MetaTrader 5.lnk")
        $lnk2.TargetPath = $mtInstalled
        $lnk2.Save()
    }
    Say "Ярлыки на рабочем столе созданы"
} catch {
    Say "Ярлыки создать не удалось: $($_.Exception.Message)"
}

# ---------------------------------------------------------------------
# 5. Памятка прямо на рабочем столе
# ---------------------------------------------------------------------
$note = @"
ЧТО ДЕЛАТЬ ДАЛЬШЕ

1. Откройте MetaTrader 5 (ярлык на рабочем столе) и войдите в свой счёт.
   Сервис -> Настройки -> Советники -> поставьте галочку
   "Разрешить автоматическую торговлю".

2. Откройте AI Scalper (ярлык на рабочем столе).
   Вкладка "Система" -> задайте ПАРОЛЬ ДАШБОРДА.
   Без пароля дашборд не работает — это защита, а не поломка.

3. Когда закрываете удалённый рабочий стол — нажимайте
   "Отключиться" (Disconnect), а НЕ "Выйти из системы" (Log off).
   Выход закрывает сеанс Windows вместе с терминалом, и торговля
   остановится. Отключение оставляет всё работать.

Программа и терминал запускаются сами при входе в Windows.
Журнал установки: $LogFile
"@
try {
    Set-Content -Path "C:\Users\Public\Desktop\ПРОЧТИ МЕНЯ.txt" -Value $note -Encoding UTF8
} catch { }

Set-Content -Path $Marker -Value (Get-Date -Format "dd.MM.yyyy HH:mm") -Encoding UTF8
Say "=== Установка завершена ==="
