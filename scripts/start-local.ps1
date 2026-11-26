param([int]$Port=8010)
$ErrorActionPreference = 'Stop'
$appDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $appDir
$pythonExe = Join-Path $appDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Zuerst scripts\setup-local.ps1 ausführen.' }
& $pythonExe manage.py check
if ($LASTEXITCODE -ne 0) { throw 'Django-Prüfung fehlgeschlagen.' }
& $pythonExe manage.py collectstatic --noinput
if ($LASTEXITCODE -ne 0) { throw 'Statische Dateien konnten nicht vorbereitet werden.' }
$workerProcess = Start-Process -FilePath $pythonExe -ArgumentList @('manage.py','worker') -WorkingDirectory $appDir -WindowStyle Hidden -PassThru
try { & $pythonExe serve.py --host 127.0.0.1 --port $Port }
finally { if (-not $workerProcess.HasExited) { Stop-Process -Id $workerProcess.Id } }
