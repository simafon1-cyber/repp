#Requires -Version 3.0
<#
    Установка советника DualGuard EA в MetaTrader 5.

    Скрипт находит все установленные терминалы MetaTrader 5 и копирует
    файл советника в папку MQL5\Experts каждого из них.

    Запуск: двойной щелчок по install.bat (рядом с этим файлом).
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

Write-Title 'Установка советника DualGuard EA в MetaTrader 5'

# --- 1. Находим файл советника рядом со скриптом ---
$projectRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path (Join-Path $projectRoot 'mql5') 'DualGuardEA.mq5'

if (-not (Test-Path -LiteralPath $source)) {
    Write-Host 'ОШИБКА: не найден файл советника.' -ForegroundColor Red
    Write-Host "  Ожидался здесь: $source"
    Write-Host ''
    Write-Host 'Запускайте установщик из папки install внутри проекта,'
    Write-Host 'не копируйте его в другое место.'
    Pause-Exit 1
}

Write-Host "Файл советника найден:" -ForegroundColor Green
Write-Host "  $source"
Write-Host ''

# --- 2. Ищем терминалы MetaTrader 5 ---
Write-Host 'Ищу установленные терминалы MetaTrader 5...'
Write-Host ''

$terminalRoot = Join-Path (Join-Path $env:APPDATA 'MetaQuotes') 'Terminal'
$targets = @()

if (Test-Path -LiteralPath $terminalRoot) {
    foreach ($dir in Get-ChildItem -LiteralPath $terminalRoot -Directory -ErrorAction SilentlyContinue) {
        # Папки Common и Community — служебные, терминалами не являются
        if ($dir.Name -in @('Common', 'Community')) { continue }
        $experts = Join-Path (Join-Path $dir.FullName 'MQL5') 'Experts'
        if (Test-Path -LiteralPath $experts) {
            $targets += $experts
        }
    }
}

# Портативные установки: MQL5\Experts лежит рядом с terminal64.exe
foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
    if ([string]::IsNullOrEmpty($base)) { continue }
    if (-not (Test-Path -LiteralPath $base)) { continue }
    foreach ($dir in Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue) {
        $experts = Join-Path (Join-Path $dir.FullName 'MQL5') 'Experts'
        $exe = Join-Path $dir.FullName 'terminal64.exe'
        if ((Test-Path -LiteralPath $experts) -and (Test-Path -LiteralPath $exe)) {
            if ($targets -notcontains $experts) { $targets += $experts }
        }
    }
}

if ($targets.Count -eq 0) {
    Write-Host 'Терминалы MetaTrader 5 не найдены автоматически.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host 'Скопируйте файл вручную — это займёт минуту:' -ForegroundColor Yellow
    Write-Host '  1. Откройте MetaTrader 5.'
    Write-Host '  2. Меню "Файл" -> "Открыть каталог данных".'
    Write-Host '  3. Зайдите в папку MQL5\Experts'
    Write-Host '  4. Скопируйте туда файл:'
    Write-Host "     $source"
    Pause-Exit 1
}

# --- 3. Копируем ---
$copied = 0
$failed = 0

foreach ($experts in $targets) {
    try {
        Copy-Item -LiteralPath $source -Destination $experts -Force
        $copied++
        Write-Host "  [OK] $experts" -ForegroundColor Green
    } catch {
        $failed++
        Write-Host "  [ОШИБКА] $experts" -ForegroundColor Red
        Write-Host "           $($_.Exception.Message)" -ForegroundColor Red
        Write-Host '           Закройте MetaTrader и запустите установщик снова.' -ForegroundColor Red
    }
}

Write-Host ''
if ($copied -eq 0) {
    Write-Host 'Не удалось скопировать ни в один терминал.' -ForegroundColor Red
    Pause-Exit 1
}

Write-Title "Готово: советник скопирован в терминалов - $copied"

Write-Host 'ЧТО ДЕЛАТЬ ДАЛЬШЕ:' -ForegroundColor Yellow
Write-Host ''
Write-Host '  1. Откройте MetaTrader 5.'
Write-Host '  2. Нажмите F4 — откроется редактор MetaEditor.'
Write-Host '  3. Слева в дереве: Experts -> DualGuardEA.mq5, откройте двойным щелчком.'
Write-Host '  4. Нажмите F7 (Компилировать). Внизу должно быть: 0 errors.'
Write-Host '  5. Вернитесь в терминал и нажмите F5 — список советников обновится.'
Write-Host '  6. Откройте график EURUSD, период M5, и перетащите на него'
Write-Host '     советник DualGuardEA из окна "Навигатор".'
Write-Host '  7. То же самое для графика XAUUSD, период M5.'
Write-Host ''
Write-Host 'ВАЖНО:' -ForegroundColor Yellow
Write-Host '  - Работайте только на ДЕМО-счёте.'
Write-Host '  - Включите кнопку "Алготрейдинг" в терминале.'
Write-Host '  - Для работы ИИ: Сервис -> Настройки -> Советники ->'
Write-Host '    "Разрешить WebRequest" и добавить http://127.0.0.1:8080'
Write-Host '  - Пока мост не настроен, поставьте параметр InpEnableAI = false.'
Write-Host ''
Write-Host 'Подробная инструкция — в файле README.md' -ForegroundColor DarkGray

if ($failed -gt 0) { Pause-Exit 1 }
Pause-Exit 0
