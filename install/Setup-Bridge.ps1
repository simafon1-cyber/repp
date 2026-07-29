#Requires -Version 3.0
<#
    Настройка Python-моста DualGuard.

    Скрипт создаёт виртуальное окружение, ставит зависимости и готовит
    файлы настроек (.env и config.toml) из образцов.

    Запуск: двойной щелчок по setup-bridge.bat (рядом с этим файлом).
#>

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

Write-Title 'Настройка Python-моста DualGuard'

$projectRoot = Split-Path -Parent $PSScriptRoot
$bridgeDir = Join-Path $projectRoot 'bridge'

if (-not (Test-Path -LiteralPath $bridgeDir)) {
    Write-Host "ОШИБКА: папка bridge не найдена: $bridgeDir" -ForegroundColor Red
    Pause-Exit 1
}

# --- 1. Ищем Python ---
$python = $null
foreach ($candidate in @('python', 'python3', 'py')) {
    try {
        $version = & $candidate --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $python = $candidate
            Write-Host "Python найден: $version" -ForegroundColor Green
            break
        }
    } catch { }
}

if ($null -eq $python) {
    Write-Host 'ОШИБКА: Python не найден.' -ForegroundColor Red
    Write-Host ''
    Write-Host 'Установите Python 3.11 или новее с сайта https://www.python.org/downloads/'
    Write-Host 'При установке ОБЯЗАТЕЛЬНО отметьте галочку "Add Python to PATH".'
    Pause-Exit 1
}

# --- 2. Виртуальное окружение ---
$venvDir = Join-Path $bridgeDir 'venv'
if (Test-Path -LiteralPath $venvDir) {
    Write-Host 'Виртуальное окружение уже существует — использую его.'
} else {
    Write-Host 'Создаю виртуальное окружение...'
    & $python -m venv $venvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'ОШИБКА: не удалось создать виртуальное окружение.' -ForegroundColor Red
        Pause-Exit 1
    }
    Write-Host 'Виртуальное окружение создано.' -ForegroundColor Green
}

# На Windows исполняемые файлы лежат в Scripts, на других системах — в bin
$venvPython = Join-Path (Join-Path $venvDir 'Scripts') 'python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    $venvPython = Join-Path (Join-Path $venvDir 'bin') 'python'
}
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host 'ОШИБКА: не найден python внутри виртуального окружения.' -ForegroundColor Red
    Pause-Exit 1
}

# --- 3. Зависимости ---
Write-Host ''
Write-Host 'Устанавливаю зависимости (это может занять пару минут)...'
$requirements = Join-Path $bridgeDir 'requirements.txt'
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -r $requirements
if ($LASTEXITCODE -ne 0) {
    Write-Host 'ОШИБКА: не удалось установить зависимости.' -ForegroundColor Red
    Write-Host 'Проверьте подключение к интернету и запустите установщик снова.'
    Pause-Exit 1
}
Write-Host 'Зависимости установлены.' -ForegroundColor Green

# --- 4. Файлы настроек из образцов ---
Write-Host ''
$envFile = Join-Path $bridgeDir '.env'
$envExample = Join-Path $bridgeDir '.env.example'
if (Test-Path -LiteralPath $envFile) {
    Write-Host 'Файл .env уже существует — не трогаю его.'
} else {
    Copy-Item -LiteralPath $envExample -Destination $envFile
    Write-Host 'Создан файл .env (ключ API пока пустой).' -ForegroundColor Green
}

$configFile = Join-Path $bridgeDir 'config.toml'
$configExample = Join-Path $bridgeDir 'config.example.toml'
if (Test-Path -LiteralPath $configFile) {
    Write-Host 'Файл config.toml уже существует — не трогаю его.'
} else {
    Copy-Item -LiteralPath $configExample -Destination $configFile
    Write-Host 'Создан файл config.toml из образца.' -ForegroundColor Green
}

Write-Title 'Мост настроен'

Write-Host 'ЧТО ДЕЛАТЬ ДАЛЬШЕ:' -ForegroundColor Yellow
Write-Host ''
Write-Host '  1. Впишите ключ Anthropic API в файл:'
Write-Host "     $envFile"
Write-Host '     Строка должна выглядеть так:  ANTHROPIC_API_KEY=sk-ant-...'
Write-Host '     Ключ можно получить на https://console.anthropic.com'
Write-Host ''
Write-Host '  2. Запустите мост двойным щелчком по файлу:'
Write-Host "     $(Join-Path $PSScriptRoot 'start-bridge.bat')"
Write-Host ''
Write-Host '  3. Проверьте в браузере: http://127.0.0.1:8080/health'
Write-Host '     Должен открыться текст со словом "ok".'
Write-Host ''
Write-Host 'ЕСЛИ КЛЮЧА API ПОКА НЕТ:' -ForegroundColor Yellow
Write-Host '  Откройте config.toml, найдите раздел [mock] и поставьте'
Write-Host '  enabled = true — мост будет отвечать заглушкой без обращения'
Write-Host '  к Claude. Это удобно для первой проверки на демо-счёте.'
Write-Host ''
Write-Host 'Файлы .env и config.toml никогда не попадают в git.' -ForegroundColor DarkGray

Pause-Exit 0
