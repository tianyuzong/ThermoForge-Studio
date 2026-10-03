param([string]$CodexPath='', [int]$Port=8877, [switch]$SkipEngineInstall)
$ErrorActionPreference='Stop'
if (!$SkipEngineInstall) {& (Join-Path $PSScriptRoot 'Install.ps1')}
& (Join-Path $PSScriptRoot 'Prepare-MCP.ps1') -Port $Port
$python=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!$CodexPath) {$CodexPath=(Get-Command codex -ErrorAction Stop).Source}
& $python -X utf8 (Join-Path $PSScriptRoot 'scripts\register_forge_plugin.py') --codex $CodexPath
if ($LASTEXITCODE -ne 0) {throw 'Plugin registration failed. The standalone MCP connection remains available in .runtime/thermoforge-mcp.json.'}
