#Requires -Version 3.0
<#
    Отключение приватного режима: вернуть шифрование секретов и спрятать
    config.py от git.

    ВАЖНО: если config.py уже был закоммичен, этот скрипт уберёт его из
    БУДУЩИХ коммитов, но НЕ удалит из истории git. Историю чистить
    отдельно — см. README.
#>

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$ErrorActionPreference = 'Stop'

function Pause-Exit($code) {
    Write-Host ''
    Write-Host 'Нажмите Enter для выхода...' -ForegroundColor DarkGray
    try { [void](Read-Host) } catch { }
    exit $code
}

Write-Host ''
Write-Host 'Отключение приватного режима' -ForegroundColor Cyan
Write-Host ''

$projectRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path (Join-Path $projectRoot 'ai_scalper_standalone') 'config.py'
$gitignorePath = Join-Path $projectRoot '.gitignore'

if (Test-Path -LiteralPath $configPath) {
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8
    if ($config -match '(?m)^PRIVATE_MODE\s*=') {
        $config = $config -replace '(?m)^PRIVATE_MODE\s*=.*$', 'PRIVATE_MODE = False'
        Set-Content -LiteralPath $configPath -Value $config -Encoding UTF8 -NoNewline
        Write-Host 'PRIVATE_MODE = False записан в config.py' -ForegroundColor Green
    }
}

if (Test-Path -LiteralPath $gitignorePath) {
    $lines = Get-Content -LiteralPath $gitignorePath -Encoding UTF8
    $out = New-Object System.Collections.Generic.List[string]
    $restored = 0
    foreach ($line in $lines) {
        if ($line.StartsWith('#ПРИВАТНЫЙ# ')) {
            $out.Add($line.Substring('#ПРИВАТНЫЙ# '.Length))
            $restored++
        } else {
            $out.Add($line)
        }
    }
    Set-Content -LiteralPath $gitignorePath -Value $out -Encoding UTF8
    Write-Host "В .gitignore возвращено правил: $restored" -ForegroundColor Green
}

Write-Host ''
Write-Host 'Готово. config.py снова прячется от git.' -ForegroundColor Green
Write-Host ''
Write-Host 'ВНИМАНИЕ: если config.py уже попадал в коммиты, он остаётся' -ForegroundColor Yellow
Write-Host 'в истории git. Скрытие от будущих коммитов историю не чистит.'
Write-Host 'Считайте ключи из него скомпрометированными и замените их.'

Pause-Exit 0
