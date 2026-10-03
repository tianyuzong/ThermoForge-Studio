$ErrorActionPreference='Stop'
$connection=Join-Path $PSScriptRoot 'local-connection.json'
if (!(Test-Path -LiteralPath $connection)) {throw 'Run Prepare-MCP.ps1 from the ThermoForge Studio installation first.'}
$settings=Get-Content -LiteralPath $connection -Raw -Encoding UTF8 | ConvertFrom-Json
$env:STRENGTH_PLUGIN_CONFIG=$connection
$env:PYTHONIOENCODING='utf-8'
& $settings.plugin_python -X utf8 (Join-Path $settings.workspace 'scripts\strength_mcp.py')
exit $LASTEXITCODE
