$ErrorActionPreference = 'Stop'
$appDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $appDir
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'uv installieren oder die manuelle Python-Anleitung in README.md verwenden.' }
uv venv --python 3.14 .venv
if ($LASTEXITCODE -ne 0) { throw 'Python-Umgebung konnte nicht erstellt werden.' }
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Installation fehlgeschlagen.' }
& .\.venv\Scripts\python.exe manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw 'Migration fehlgeschlagen.' }
& .\.venv\Scripts\python.exe manage.py seed_demo
if ($LASTEXITCODE -ne 0) { throw 'Demo konnte nicht angelegt werden.' }
Write-Host 'Bereit: scripts\start-local.ps1. Zugang: demo / Projektakte-Demo-2026!'
