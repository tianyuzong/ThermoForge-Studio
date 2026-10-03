$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
$python=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) {throw 'Run Install.ps1 first.'}
& $python -c "import json,sys; from strength.backends import capabilities,resolve_backend; from strength.config import DATA,CPU_THREADS,MAX_DOFS; b=resolve_backend(); print(json.dumps({'python':sys.version,'data':str(DATA),'cpu_threads':CPU_THREADS,'max_dofs':MAX_DOFS,'solver':b.metadata()},ensure_ascii=False,indent=2))"
if ($LASTEXITCODE -ne 0) {throw 'CPU dependency check failed.'}
$cli=Get-Command codex -ErrorAction SilentlyContinue
if ($cli) { & $cli.Source --version; & $cli.Source login status } else {Write-Output 'Codex CLI was not found in PATH; configure STRENGTH_CODEX_COMMAND in local-settings.json.'}
