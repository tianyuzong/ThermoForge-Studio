$ErrorActionPreference='Stop'
$state=Join-Path $PSScriptRoot '.runtime\server.json'
if (!(Test-Path -LiteralPath $state)) {Write-Output 'No managed server record.'; exit 0}
$record=Get-Content -Raw -LiteralPath $state | ConvertFrom-Json
$process=Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)" -ErrorAction SilentlyContinue
if ($process) {
    if ($process.ExecutablePath -ne $record.python -or $process.CommandLine -notlike '*strength.server:app*') {throw 'PID now belongs to another process; refusing to stop it.'}
    Start-Process -FilePath 'taskkill.exe' -ArgumentList @('/PID',"$($record.pid)",'/T','/F') -WindowStyle Hidden -Wait
    Write-Output 'ThermoForge Studio and its compute workers stopped.'
} else {Write-Output 'Server is not running.'}
Remove-Item -LiteralPath $state
