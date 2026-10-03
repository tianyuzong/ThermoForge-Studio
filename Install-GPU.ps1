$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
$python=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) { & (Join-Path $PSScriptRoot 'Install.ps1') }
& $python -m pip install -r requirements-gpu.lock
if ($LASTEXITCODE -ne 0) {throw 'GPU dependency installation failed.'}
& $python -m strength.cuda_runtime
if ($LASTEXITCODE -ne 0) {throw 'CUDA/cuDSS self-test failed. See the diagnostic above. No CPU fallback was used.'}
Write-Output 'GPU solver ready. Select Native CUDA + cuDSS in Compute Resources, or request GPU in the Agent prompt.'
