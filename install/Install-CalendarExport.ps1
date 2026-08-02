#Requires -Version 3.0
<#
    Установка сервиса CalendarExport в MetaTrader 5.

    Сервис выгружает ВСТРОЕННЫЙ экономический календарь терминала в файл,
    который читает программа AI Scalper. Это бесплатный источник новостей:
    без API-ключа, без регистрации, без лимитов запросов.

    Зачем нужен сервис: python-библиотека MetaTrader5 календарь не отдаёт,
    функции календаря существуют только в MQL5.

    Запуск: двойной щелчок по install-calendar.bat (рядом с этим файлом).
#>

# Русский текст в консоли отображается корректно только при UTF-8
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$ErrorActionPreference = 'Stop'

function Write-Title($text) {
    Write-Host ''
    Write-Host ('=' * 55) -ForegroundColor Cyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ('=' * 55) -ForegroundColor Cyan
    Write-Host ''
}

function Pause-Exit($code) {
    Write-Host ''
    Write-Host 'Нажмите Enter для выхода...' -ForegroundColor DarkGray
    try { [void](Read-Host) } catch { }
    exit $code
}

Write-Title 'Установка сервиса CalendarExport (бесплатный календарь MT5)'

# --- 1. Находим файл сервиса рядом со скриптом ---
$projectRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path (Join-Path $projectRoot 'mql5') 'CalendarExport.mq5'

if (-not (Test-Path -LiteralPath $source)) {
    Write-Host 'ОШИБКА: не найден файл сервиса.' -ForegroundColor Red
    Write-Host "  Ожидался здесь: $source"
    Write-Host ''
    Write-Host 'Запускайте установщик из папки install внутри проекта,'
    Write-Host 'не копируйте его в другое место.'
    Pause-Exit 1
}

Write-Host 'Файл сервиса найден:' -ForegroundColor Green
Write-Host "  $source"
Write-Host ''

# --- 2. Ищем терминалы MetaTrader 5 ---
# Сервисы лежат в MQL5\Services (а не в Experts, как советники)
Write-Host 'Ищу установленные терминалы MetaTrader 5...'
Write-Host ''

$terminalRoot = Join-Path (Join-Path $env:APPDATA 'MetaQuotes') 'Terminal'
$targets = @()

if (Test-Path -LiteralPath $terminalRoot) {
    foreach ($dir in Get-ChildItem -LiteralPath $terminalRoot -Directory -ErrorAction SilentlyContinue) {
        # Папки Common и Community — служебные, терминалами не являются
        if ($dir.Name -in @('Common', 'Community')) { continue }
        $mql5 = Join-Path $dir.FullName 'MQL5'
        if (-not (Test-Path -LiteralPath $mql5)) { continue }
        $services = Join-Path $mql5 'Services'
        # Папки Services может не быть в свежей установке — создаём
        if (-not (Test-Path -LiteralPath $services)) {
            try { New-Item -ItemType Directory -Path $services -Force | Out-Null } catch { continue }
        }
        $targets += $services
    }
}

# Портативные установки: MQL5 лежит рядом с terminal64.exe
foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
    if ([string]::IsNullOrEmpty($base)) { continue }
    if (-not (Test-Path -LiteralPath $base)) { continue }
    foreach ($dir in Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue) {
        $exe = Join-Path $dir.FullName 'terminal64.exe'
        $mql5 = Join-Path $dir.FullName 'MQL5'
        if ((Test-Path -LiteralPath $exe) -and (Test-Path -LiteralPath $mql5)) {
            $services = Join-Path $mql5 'Services'
            if (-not (Test-Path -LiteralPath $services)) {
                try { New-Item -ItemType Directory -Path $services -Force | Out-Null } catch { continue }
            }
            if ($targets -notcontains $services) { $targets += $services }
        }
    }
}

if ($targets.Count -eq 0) {
    Write-Host 'Терминалы MetaTrader 5 не найдены автоматически.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host 'Скопируйте файл вручную — это займёт минуту:' -ForegroundColor Yellow
    Write-Host '  1. Откройте MetaTrader 5.'
    Write-Host '  2. Меню "Файл" -> "Открыть каталог данных".'
    Write-Host '  3. Зайдите в папку MQL5\Services (создайте, если её нет).'
    Write-Host '  4. Скопируйте туда файл:'
    Write-Host "     $source"
    Pause-Exit 1
}

# --- 3. Копируем ---
$copied = 0
$failed = 0

foreach ($services in $targets) {
    try {
        Copy-Item -LiteralPath $source -Destination $services -Force
        $copied++
        Write-Host "  [OK] $services" -ForegroundColor Green
    } catch {
        $failed++
        Write-Host "  [ОШИБКА] $services" -ForegroundColor Red
        Write-Host "           $($_.Exception.Message)" -ForegroundColor Red
        Write-Host '           Закройте MetaTrader и запустите установщик снова.' -ForegroundColor Red
    }
}

Write-Host ''
if ($copied -eq 0) {
    Write-Host 'Не удалось скопировать ни в один терминал.' -ForegroundColor Red
    Pause-Exit 1
}

Write-Title "Готово: сервис скопирован в терминалов - $copied"

Write-Host 'ЧТО ДЕЛАТЬ ДАЛЬШЕ:' -ForegroundColor Yellow
Write-Host ''
Write-Host '  1. Откройте MetaTrader 5.'
Write-Host '  2. Нажмите F4 — откроется редактор MetaEditor.'
Write-Host '  3. Слева в дереве: Services -> CalendarExport.mq5,'
Write-Host '     откройте двойным щелчком.'
Write-Host '  4. Нажмите F7 (Компилировать). Внизу должно быть: 0 errors.'
Write-Host '  5. Вернитесь в терминал, откройте окно "Навигатор" (Ctrl+N).'
Write-Host '  6. Раздел "Сервисы" -> правой кнопкой по CalendarExport ->'
Write-Host '     "Добавить сервис" -> ОК.'
Write-Host '  7. Ещё раз правой кнопкой по нему -> "Запустить".'
Write-Host ''
Write-Host 'КАК ПРОВЕРИТЬ, ЧТО РАБОТАЕТ:' -ForegroundColor Yellow
Write-Host ''
Write-Host '  - Вкладка "Журнал" внизу терминала: должна появиться строка'
Write-Host '    "CalendarExport: записано событий: N".'
Write-Host '  - В программе AI Scalper: вкладка "Новости" -> "Обновить календарь".'
Write-Host '    Внизу должно быть написано, что источник — календарь MetaTrader 5.'
Write-Host ''
Write-Host 'ЕСЛИ СОБЫТИЙ НОЛЬ:' -ForegroundColor Yellow
Write-Host '  - На выходных это нормально, новостей действительно нет.'
Write-Host '  - Проверьте, что календарь включён: Сервис -> Настройки ->'
Write-Host '    вкладка "Сервер" -> терминал должен быть подключён.'
Write-Host ''
Write-Host 'Сервис ничего не торгует — он только читает календарь' -ForegroundColor DarkGray
Write-Host 'и пишет один файл. Разрешение на автоторговлю ему не нужно.' -ForegroundColor DarkGray

if ($failed -gt 0) { Pause-Exit 1 }
Pause-Exit 0
