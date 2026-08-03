#Requires -Version 3.0
<#
    Приватный режим: хранить свои настройки и ключи прямо в репозитории.

    ЧТО ДЕЛАЕТ СКРИПТ
      1. Ставит PRIVATE_MODE = True в config.py — секреты перестают
         шифроваться и пишутся открытым текстом.
      2. Убирает config.py из .gitignore, чтобы он начал сохраняться в git.

    ВКЛЮЧАТЬ ТОЛЬКО ПОСЛЕ ТОГО, КАК РЕПОЗИТОРИЙ СТАЛ ПРИВАТНЫМ.
    Скрипт переспросит об этом.

    Отменить: disable-private-mode.bat
#>

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$ErrorActionPreference = 'Stop'

function Write-Title($text) {
    Write-Host ''
    Write-Host ('=' * 60) -ForegroundColor Cyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ('=' * 60) -ForegroundColor Cyan
    Write-Host ''
}

function Pause-Exit($code) {
    Write-Host ''
    Write-Host 'Нажмите Enter для выхода...' -ForegroundColor DarkGray
    try { [void](Read-Host) } catch { }
    exit $code
}

Write-Title 'Включение приватного режима'

$projectRoot = Split-Path -Parent $PSScriptRoot
$appDir = Join-Path $projectRoot 'ai_scalper_standalone'
$configPath = Join-Path $appDir 'config.py'
$examplePath = Join-Path $appDir 'config.py.example'
$gitignorePath = Join-Path $projectRoot '.gitignore'

Write-Host 'ЧТО ИЗМЕНИТСЯ:' -ForegroundColor Yellow
Write-Host '  - Ключи и пароли будут храниться в config.py ОТКРЫТЫМ ТЕКСТОМ.'
Write-Host '  - config.py начнёт сохраняться в git вместе с остальным кодом.'
Write-Host ''
Write-Host 'ЧТО НЕ ИЗМЕНИТСЯ:' -ForegroundColor Yellow
Write-Host '  - telegram_session, accounts.json, журналы и CSV сделок'
Write-Host '    останутся вне git. Это живые пропуска, им там не место.'
Write-Host ''
Write-Host 'ВАЖНО: репозиторий на GitHub должен быть уже ПРИВАТНЫМ.' -ForegroundColor Red
Write-Host 'Settings -> General -> Danger Zone -> Change visibility -> Private'
Write-Host ''

$answer = Read-Host 'Репозиторий уже приватный? Введите "да" для продолжения'
if ($answer -ne 'да' -and $answer -ne 'da' -and $answer -ne 'yes') {
    Write-Host ''
    Write-Host 'Отменено. Сначала сделайте репозиторий приватным.' -ForegroundColor Yellow
    Pause-Exit 0
}

# --- 1. config.py ---
if (-not (Test-Path -LiteralPath $configPath)) {
    if (Test-Path -LiteralPath $examplePath) {
        Copy-Item -LiteralPath $examplePath -Destination $configPath
        Write-Host 'Создан config.py из config.py.example' -ForegroundColor Green
    } else {
        Write-Host "ОШИБКА: не найден ни config.py, ни config.py.example в $appDir" -ForegroundColor Red
        Pause-Exit 1
    }
}

$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8
if ($config -match '(?m)^PRIVATE_MODE\s*=') {
    $config = $config -replace '(?m)^PRIVATE_MODE\s*=.*$', 'PRIVATE_MODE = True'
} else {
    $config = $config.TrimEnd() + "`r`n`r`n# Приватный режим: секреты хранятся открытым текстом`r`nPRIVATE_MODE = True`r`n"
}
Set-Content -LiteralPath $configPath -Value $config -Encoding UTF8 -NoNewline
Write-Host 'PRIVATE_MODE = True записан в config.py' -ForegroundColor Green

# --- 2. .gitignore ---
if (-not (Test-Path -LiteralPath $gitignorePath)) {
    Write-Host "ОШИБКА: не найден .gitignore в $projectRoot" -ForegroundColor Red
    Pause-Exit 1
}

$lines = Get-Content -LiteralPath $gitignorePath -Encoding UTF8
$out = New-Object System.Collections.Generic.List[string]
$inBlock = $false
$removed = 0
foreach ($line in $lines) {
    if ($line -like '*НАЧАЛО БЛОКА ПРИВАТНОГО РЕЖИМА*') { $inBlock = $true; $out.Add($line); continue }
    if ($line -like '*КОНЕЦ БЛОКА ПРИВАТНОГО РЕЖИМА*')  { $inBlock = $false; $out.Add($line); continue }
    # Внутри блока комментарии оставляем, а сами правила прячем — так блок
    # можно вернуть обратно скриптом отключения.
    if ($inBlock -and $line.Trim() -ne '' -and -not $line.TrimStart().StartsWith('#')) {
        $out.Add('#ПРИВАТНЫЙ# ' + $line)
        $removed++
        continue
    }
    $out.Add($line)
}

if ($removed -eq 0) {
    Write-Host 'В .gitignore уже нет правил для config.py — похоже, режим уже включён.' -ForegroundColor Yellow
} else {
    Set-Content -LiteralPath $gitignorePath -Value $out -Encoding UTF8
    Write-Host "Из .gitignore убрано правил: $removed" -ForegroundColor Green
}

Write-Title 'Готово'

Write-Host 'ЧТО СДЕЛАТЬ ДАЛЬШЕ:' -ForegroundColor Yellow
Write-Host ''
Write-Host '  1. Впишите свои ключи в программе (вкладки «Источники», «Брокер»).'
Write-Host '  2. Сохраните изменения в git:'
Write-Host '       git add -A'
Write-Host '       git commit -m "Мои настройки"'
Write-Host '       git push'
Write-Host ''
Write-Host 'ПРОВЕРЬТЕ ПЕРЕД PUSH:' -ForegroundColor Red
Write-Host '  git status  — в списке НЕ должно быть telegram_session,'
Write-Host '  accounts.json и файлов .log'
Write-Host ''
Write-Host 'Отменить режим: disable-private-mode.bat' -ForegroundColor DarkGray

Pause-Exit 0
