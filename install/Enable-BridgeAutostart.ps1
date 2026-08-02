#Requires -Version 3.0
<#
    Автозапуск моста AI Scalper Pro при входе в Windows.

    Создаёт задачу в Планировщике заданий Windows, которая запускает мост
    в фоне (без чёрного окна) сразу после входа в систему. Мост нужен
    советнику для получения внешнего сигнала.

    Запуск: двойной щелчок по enable-bridge-autostart.bat
    Отключить: disable-bridge-autostart.bat

    -DryRun — только показать, что будет сделано, ничего не менять.
#>
param(
    [switch]$DryRun
)

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$ErrorActionPreference = 'Stop'
$TaskName = 'AI-Scalper-Bridge'

function Write-Title($text) {
    Write-Host ''
    Write-Host ('=' * 55) -ForegroundColor Cyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ('=' * 55) -ForegroundColor Cyan
    Write-Host ''
}

function Pause-Exit($code) {
    if (-not $DryRun) {
        Write-Host ''
        Write-Host 'Нажмите Enter для выхода...' -ForegroundColor DarkGray
        try { [void](Read-Host) } catch { }
    }
    exit $code
}

Write-Title 'Автозапуск моста AI Scalper Pro'

# --- 1. Находим мост ---
$projectRoot = Split-Path -Parent $PSScriptRoot
$bridgeDir = Join-Path (Join-Path $projectRoot 'ai_scalper_pro') 'bridge'
$bridgeScript = Join-Path $bridgeDir 'bridge_example.py'

if (-not (Test-Path -LiteralPath $bridgeScript)) {
    Write-Host 'ОШИБКА: не найден файл моста.' -ForegroundColor Red
    Write-Host "  Ожидался здесь: $bridgeScript"
    Write-Host ''
    Write-Host 'Запускайте скрипт из папки install внутри проекта.'
    Pause-Exit 1
}
Write-Host 'Мост найден:' -ForegroundColor Green
Write-Host "  $bridgeScript"

# --- 2. Ищем Python (окружение проекта в приоритете) ---
# pythonw.exe запускает скрипт БЕЗ чёрного окна консоли — для фоновой
# задачи это важно, иначе окно висело бы на экране после каждого входа.
$python = $null
$candidates = @(
    (Join-Path (Join-Path (Join-Path $bridgeDir 'venv') 'Scripts') 'pythonw.exe'),
    (Join-Path (Join-Path (Join-Path $bridgeDir 'venv') 'Scripts') 'python.exe')
)
foreach ($c in $candidates) {
    if (Test-Path -LiteralPath $c) { $python = $c; break }
}
if ($null -eq $python) {
    # pythonw/pyw — варианты без окна консоли, их проверяем первыми.
    # py.exe — штатный лаунчер Python на Windows, есть почти всегда.
    # python3 — для проверки скрипта не на Windows.
    foreach ($name in @('pythonw.exe', 'pythonw', 'pyw.exe', 'python.exe', 'py.exe', 'python3')) {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        if ($found) { $python = $found.Source; break }
    }
}
if ($null -eq $python) {
    Write-Host ''
    Write-Host 'ОШИБКА: Python не найден.' -ForegroundColor Red
    Write-Host 'Установите Python с https://www.python.org/downloads/'
    Write-Host 'и обязательно отметьте "Add Python to PATH".'
    Pause-Exit 1
}
Write-Host ''
Write-Host 'Python найден:' -ForegroundColor Green
Write-Host "  $python"

# --- 3. Проверяем зависимости и файл настроек ---
$envFile = Join-Path $bridgeDir '.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    $envExample = Join-Path $bridgeDir '.env.example'
    if ((Test-Path -LiteralPath $envExample) -and (-not $DryRun)) {
        Copy-Item -LiteralPath $envExample -Destination $envFile
        Write-Host ''
        Write-Host 'Создан файл .env — впишите в него ключ Twelve Data.' -ForegroundColor Yellow
    }
}

if ($DryRun) {
    Write-Host ''
    Write-Host 'РЕЖИМ ПРОВЕРКИ: задача НЕ создаётся.' -ForegroundColor Yellow
    Write-Host "  Имя задачи:      $TaskName"
    Write-Host "  Команда:         $python"
    Write-Host "  Аргумент:        $bridgeScript"
    Write-Host "  Рабочая папка:   $bridgeDir"
    Write-Host "  Запуск:          при входе пользователя в Windows"
    exit 0
}

# --- 4. Регистрируем задачу в Планировщике ---
try {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host ''
        Write-Host 'Старая задача автозапуска удалена (будет создана заново).'
    }

    $action = New-ScheduledTaskAction -Execute $python `
                                      -Argument "`"$bridgeScript`"" `
                                      -WorkingDirectory $bridgeDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    # Задача должна пережить работу от батареи и не глушиться системой
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                                             -DontStopIfGoingOnBatteries `
                                             -StartWhenAvailable `
                                             -ExecutionTimeLimit ([TimeSpan]::Zero)

    Register-ScheduledTask -TaskName $TaskName `
                           -Action $action `
                           -Trigger $trigger `
                           -Settings $settings `
                           -Description 'Локальный мост внешнего сигнала для советника AI Scalper Pro' | Out-Null

    Write-Host ''
    Write-Host 'Задача автозапуска создана.' -ForegroundColor Green

    Start-ScheduledTask -TaskName $TaskName
    Write-Host 'Мост запущен прямо сейчас (ждать перезагрузки не нужно).' -ForegroundColor Green
} catch {
    Write-Host ''
    Write-Host 'ОШИБКА при создании задачи автозапуска:' -ForegroundColor Red
    Write-Host "  $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ''
    Write-Host 'Чаще всего помогает запуск от имени администратора:'
    Write-Host '  правый клик по enable-bridge-autostart.bat -> "Запуск от имени администратора"'
    Pause-Exit 1
}

Write-Title 'Готово'

Write-Host 'Мост теперь запускается сам при каждом входе в Windows.'
Write-Host ''
Write-Host 'ПРОВЕРЬТЕ:' -ForegroundColor Yellow
Write-Host '  Откройте в браузере http://127.0.0.1:8787/health'
Write-Host '  Должен появиться текст со словом "ok".'
Write-Host '  Если "api_key_present": false — впишите ключ в файл:'
Write-Host "     $envFile"
Write-Host ''
Write-Host 'ЧТОБЫ ОТКЛЮЧИТЬ автозапуск:' -ForegroundColor Yellow
Write-Host '  запустите disable-bridge-autostart.bat'
Write-Host ''
Write-Host 'Журнал работы моста:' -ForegroundColor DarkGray
Write-Host "  $(Join-Path $bridgeDir 'bridge.log')"

Pause-Exit 0
