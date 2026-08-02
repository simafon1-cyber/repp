#Requires -Version 3.0
<#
    Отключает автозапуск моста AI Scalper Pro и останавливает его.
    Запуск: двойной щелчок по disable-bridge-autostart.bat
#>

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$ErrorActionPreference = 'Stop'
$TaskName = 'AI-Scalper-Bridge'

Write-Host ''
Write-Host ('=' * 55) -ForegroundColor Cyan
Write-Host '  Отключение автозапуска моста' -ForegroundColor Cyan
Write-Host ('=' * 55) -ForegroundColor Cyan
Write-Host ''

$code = 0
try {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        Write-Host 'Автозапуск и так не настроен — ничего делать не нужно.' -ForegroundColor Yellow
    } else {
        try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch { }
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host 'Автозапуск отключён, задача удалена.' -ForegroundColor Green
        Write-Host ''
        Write-Host 'Уже запущенный мост может продолжать работать до перезагрузки.'
        Write-Host 'Чтобы остановить его сейчас: Диспетчер задач -> найдите'
        Write-Host 'процесс pythonw.exe (или python.exe) -> "Снять задачу".'
    }
} catch {
    Write-Host 'ОШИБКА:' -ForegroundColor Red
    Write-Host "  $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ''
    Write-Host 'Попробуйте запустить от имени администратора.'
    $code = 1
}

Write-Host ''
Write-Host 'Нажмите Enter для выхода...' -ForegroundColor DarkGray
try { [void](Read-Host) } catch { }
exit $code
