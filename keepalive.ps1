# ClubRomance — watchdog: поднимает бота, если он не запущен.
# Запускается Планировщиком Windows каждые 5 минут (задача "ClubRomanceBot").
$ErrorActionPreference = "SilentlyContinue"
$proj = "S:\ClubRomance"

$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*run.py*' }

if (-not $running) {
    # Запуск скрыто; stdout+stderr -> runtime.log (как при ручном запуске).
    Start-Process -FilePath "cmd.exe" `
        -ArgumentList '/c', 'set PYTHONIOENCODING=utf-8 && python run.py > runtime.log 2>&1' `
        -WorkingDirectory $proj -WindowStyle Hidden
}
