param([int]$Port=8877)
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
$python=Join-Path $root '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) {throw 'Run Install.ps1 first.'}
$pluginPython=Join-Path $root '.runtime\plugin-env\Scripts\python.exe'
if (!(Test-Path -LiteralPath $pluginPython)) {
    & $python -m venv (Join-Path $root '.runtime\plugin-env')
    if ($LASTEXITCODE -ne 0) {throw 'Plugin environment creation failed.'}
}
& $pluginPython -m pip install -r (Join-Path $root 'requirements-plugin.lock')
if ($LASTEXITCODE -ne 0) {throw 'MCP dependency installation failed.'}
$plugin=Join-Path $root 'integrations\thermoforge-studio'
@{workspace=$root;url="http://127.0.0.1:$Port";plugin_python=$pluginPython} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $plugin 'local-connection.json') -Encoding UTF8
$config=@{mcpServers=@{'thermoforge-studio'=@{command='powershell.exe';args=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',(Join-Path $plugin 'Start-Mcp.ps1'));cwd=$plugin}}}
$config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $root '.runtime\thermoforge-mcp.json') -Encoding UTF8
Write-Output "MCP configuration: $root\.runtime\thermoforge-mcp.json"
