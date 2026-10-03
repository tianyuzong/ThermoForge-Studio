param([string]$Nsys='C:\Program Files\NVIDIA Corporation\Nsight Systems 2024.4.2\target-windows-x64\nsys.exe')
$ErrorActionPreference='Stop'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=[Security.Principal.WindowsPrincipal]::new($identity)
if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {throw 'NVIDIA Nsight Systems Windows CLI requires administrator privileges. Run this script from an elevated PowerShell; ordinary GPU simulation does not require elevation.'}
$root=Split-Path -Parent $PSScriptRoot
Set-Location $root
if (!(Test-Path -LiteralPath $Nsys)) {throw 'Pass -Nsys with the installed NVIDIA Nsight Systems nsys.exe path.'}
& $Nsys profile --trace=cuda,nvtx --sample=none --cpuctxsw=none --cuda-memory-usage=true --force-overwrite=true --output=.runtime/gpu-trace .venv/Scripts/python.exe -u scripts/gpu_benchmark.py --targets 500000 --repeats 1 --gpu-only --output docs/GPU_PROFILE_RUN.json
if ($LASTEXITCODE -ne 0) {throw 'Nsight capture failed; do not claim kernel trace verification.'}
& $Nsys stats --report cuda_gpu_kern_sum,cuda_gpu_mem_time_sum --format csv --output docs/gpu-trace .runtime/gpu-trace.nsys-rep
if ($LASTEXITCODE -ne 0) {throw 'Nsight trace statistics export failed.'}
