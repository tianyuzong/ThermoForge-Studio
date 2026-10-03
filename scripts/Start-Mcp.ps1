$ErrorActionPreference='Stop'
$configPath=$env:STRENGTH_PLUGIN_CONFIG
if (!$configPath) { $configPath=Join-Path $env:LOCALAPPDATA 'StrengthStudio\plugin.json' }
if (!(Test-Path -LiteralPath $configPath)) { throw 'Strength Studio plugin is not configured. Run Install-CodexPlugin.ps1 in your source checkout.' }
$settings=Get-Content -Raw -LiteralPath $configPath -Encoding UTF8 | ConvertFrom-Json
$pluginPython=$settings.plugin_python
if (!$pluginPython -or !(Test-Path -LiteralPath $pluginPython)) { throw 'Plugin Python environment missing. Run Install-CodexPlugin.ps1 again.' }
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
# Only the MCP process writes stdout. Diagnostics go to stderr.
& $pluginPython -X utf8 (Join-Path $PSScriptRoot 'strength_mcp.py')
exit $LASTEXITCODE
