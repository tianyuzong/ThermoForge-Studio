param([int]$Port=8877,[switch]$NoBrowser,[string]$PythonPath='')
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
if (!$PythonPath) { $PythonPath=Join-Path $root '.venv\Scripts\python.exe' }
if (!(Test-Path -LiteralPath $PythonPath)) { & (Join-Path $root 'Install.ps1'); if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {throw 'Installation failed'} }
if (!(Test-Path -LiteralPath $PythonPath)) {throw "Python environment not found: $PythonPath"}
$runtime=Join-Path $root '.runtime'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$url="http://127.0.0.1:$Port"
try { $health=Invoke-RestMethod "$url/api/v1/health" -TimeoutSec 2 } catch { $health=$null }
if ($health -and $health.app -eq 'ThermoForge Studio') {
    Write-Output "ThermoForge Studio is already running: $url"
    if (!$NoBrowser) {Start-Process $url}
    exit 0
}
$env:PYTHONIOENCODING='utf-8'
$controlSources=@{}
foreach ($relative in @('strength/server.py','forge/api.py','forge/runs.py','forge/plans.py')) {
    $controlSources[$relative]=(Get-FileHash -LiteralPath (Join-Path $root $relative) -Algorithm SHA256).Hash.ToLower()
}
$process=Start-Process -FilePath $PythonPath -ArgumentList @('-m','uvicorn','strength.server:app','--host','127.0.0.1','--port',"$Port") -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime 'server.log') -RedirectStandardError (Join-Path $runtime 'server-error.log')
@{pid=$process.Id;port=$Port;python=(Resolve-Path -LiteralPath $PythonPath).Path;source_hashes=$controlSources} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtime 'server.json') -Encoding UTF8
for ($attempt=0;$attempt -lt 60;$attempt++) {
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) { throw "Server exited. See $runtime\server-error.log" }
    try { $health=Invoke-RestMethod "$url/api/v1/health" -TimeoutSec 1; if ($health.ok) {break} } catch {}
}
if (!$health.ok) {throw "Server startup timed out. See $runtime\server-error.log"}
Write-Output "ThermoForge Studio is ready: $url"
if (!$NoBrowser) {Start-Process $url}
