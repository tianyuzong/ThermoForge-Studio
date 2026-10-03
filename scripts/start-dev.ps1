param([int]$Port=8876)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Set-Location $root
& "$root\.venv\Scripts\python.exe" -m uvicorn strength.server:app --host 127.0.0.1 --port $Port
