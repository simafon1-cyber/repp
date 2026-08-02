#Requires -Version 3.0
<#
    Установка советника AI Scalper Pro в MetaTrader 5.

    Проект может лежать где угодно (например D:\Big Projeckt) — MetaTrader
    читает советники только из своего каталога данных, поэтому скрипт
    находит все установленные терминалы и копирует туда файлы советника.

    Запуск: двойной щелчок по install-scalper.bat (рядом с этим файлом).
#>

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$ErrorActionPreference = 'Stop'
$EAFolderName = 'AI_Scalper_Pro'

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

Write-Title 'Установка советника AI Scalper Pro в MetaTrader 5'

# --- 1. Собираем файлы советника ---
$projectRoot = Split-Path -Parent $PSScriptRoot
$sourceDir = Join-Path $projectRoot 'ai_scalper_pro'

if (-not (Test-Path -LiteralPath $sourceDir)) {
    Write-Host 'ОШИБКА: не найдена папка ai_scalper_pro.' -ForegroundColor Red
    Write-Host "  Ожидалась здесь: $sourceDir"
    Write-Host ''
    Write-Host 'Запускайте установщик из папки install внутри проекта,'
    Write-Host 'не копируйте его в другое место.'
    Pause-Exit 1
}

# Советник состоит из главного .mq5 и нескольких .mqh — нужны ВСЕ,
# они подключаются через #include и должны лежать в одной папке.
$files = @()
$files += Get-ChildItem -LiteralPath $sourceDir -Filter '*.mq5' -File -ErrorAction SilentlyContinue
$files += Get-ChildItem -LiteralPath $sourceDir -Filter '*.mqh' -File -ErrorAction SilentlyContinue

if ($files.Count -eq 0) {
    Write-Host 'ОШИБКА: в папке ai_scalper_pro нет файлов .mq5 / .mqh' -ForegroundColor Red
    Pause-Exit 1
}

$mainFile = $files | Where-Object { $_.Extension -eq '.mq5' } | Select-Object -First 1
Write-Host "Файлов советника найдено: $($files.Count)" -ForegroundColor Green
Write-Host "  Главный файл: $($mainFile.Name)"
Write-Host "  Папка: $sourceDir"

# --- 2. Ищем терминалы MetaTrader 5 ---
Write-Host ''
Write-Host 'Ищу установленные терминалы MetaTrader 5...'
Write-Host ''

$terminalRoot = Join-Path (Join-Path $env:APPDATA 'MetaQuotes') 'Terminal'
$targets = @()

if (Test-Path -LiteralPath $terminalRoot) {
    foreach ($dir in Get-ChildItem -LiteralPath $terminalRoot -Directory -ErrorAction SilentlyContinue) {
        if ($dir.Name -in @('Common', 'Community')) { continue }
        $experts = Join-Path (Join-Path $dir.FullName 'MQL5') 'Experts'
        if (Test-Path -LiteralPath $experts) { $targets += $experts }
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
    Write-Host 'Скопируйте файлы вручную — это займёт минуту:' -ForegroundColor Yellow
    Write-Host '  1. Откройте MetaTrader 5.'
    Write-Host '  2. Меню "Файл" -> "Открыть каталог данных".'
    Write-Host "  3. Создайте папку MQL5\Experts\$EAFolderName"
    Write-Host '  4. Скопируйте туда ВСЕ файлы .mq5 и .mqh из папки:'
    Write-Host "     $sourceDir"
    Pause-Exit 1
}

# --- 3. Копируем ---
$copied = 0
$failed = 0

foreach ($experts in $targets) {
    $destination = Join-Path $experts $EAFolderName
    try {
        if (-not (Test-Path -LiteralPath $destination)) {
            New-Item -ItemType Directory -Path $destination -Force | Out-Null
        }
        foreach ($f in $files) {
            Copy-Item -LiteralPath $f.FullName -Destination $destination -Force
        }
        $copied++
        Write-Host "  [OK] $destination" -ForegroundColor Green
    } catch {
        $failed++
        Write-Host "  [ОШИБКА] $destination" -ForegroundColor Red
        Write-Host "           $($_.Exception.Message)" -ForegroundColor Red
        Write-Host '           Закройте MetaTrader и запустите установщик снова.' -ForegroundColor Red
    }
}

Write-Host ''
if ($copied -eq 0) {
    Write-Host 'Не удалось скопировать ни в один терминал.' -ForegroundColor Red
    Pause-Exit 1
}

Write-Title "Готово: терминалов обновлено - $copied"

Write-Host 'ЧТО ДЕЛАТЬ ДАЛЬШЕ:' -ForegroundColor Yellow
Write-Host ''
Write-Host '  1. Откройте MetaTrader 5.'
Write-Host '  2. Нажмите F4 — откроется редактор MetaEditor.'
Write-Host "  3. Слева в дереве: Experts -> $EAFolderName -> $($mainFile.Name)"
Write-Host '     Откройте двойным щелчком.'
Write-Host '  4. Нажмите F7 (Компилировать). Внизу должно быть: 0 errors.'
Write-Host '  5. Вернитесь в терминал и нажмите F5 — список советников обновится.'
Write-Host '  6. Перетащите советник на график из окна "Навигатор".'
Write-Host ''
Write-Host 'ВАЖНО:' -ForegroundColor Yellow
Write-Host '  - Работайте только на ДЕМО-счёте.'
Write-Host '  - Включите кнопку "Алготрейдинг" в терминале.'
Write-Host '  - Для внешнего сигнала нужен мост:'
Write-Host '    запустите enable-bridge-autostart.bat в этой же папке,'
Write-Host '    затем разрешите WebRequest для http://127.0.0.1:8787'
Write-Host '    (Сервис -> Настройки -> Советники).'
Write-Host ''
Write-Host 'Что изменилось в этой версии — см. ai_scalper_pro\README.md' -ForegroundColor DarkGray

if ($failed -gt 0) { Pause-Exit 1 }
Pause-Exit 0
