param([switch]$DevBuild,[switch]$GPU)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
if ($PSScriptRoot.Length -gt 140 -and (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -ErrorAction SilentlyContinue).LongPathsEnabled -ne 1) {
    throw 'This installation path exceeds the Windows path limit for scientific Python wheels. Extract the source to a path shorter than 140 characters, for example C:\ThermoForgeStudio. No registry change is required.'
}
Set-Location $PSScriptRoot
$python=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) {
    & python -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'; assert sys.maxsize > 2**32, '64-bit Python required'"
    if ($LASTEXITCODE -ne 0) {throw 'Install Python 3.11+ (64-bit) first.'}
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) {throw 'Could not create the Python environment.'}
}
$requirements='requirements.txt'
$version=& $python -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($version -eq '3.14') {$requirements='requirements-windows-py314.lock'}
else {Write-Warning 'This release is CPU-validated on Windows x64 / Python 3.14. Other Python versions use unvalidated dependency resolution.'}
& $python -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) {throw 'Python dependency installation failed.'}
$solver=Join-Path $PSScriptRoot 'vendor\ccx.exe'
if (!(Test-Path -LiteralPath $solver)) {
    $cache=Join-Path $PSScriptRoot '.runtime\downloads'
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $archive=Join-Path $cache 'calculix_2.23_4win.zip'
    Write-Output 'Downloading official CalculiX 2.23 Windows distribution...'
    Invoke-WebRequest 'https://www.dhondt.de/calculix_2.23_4win.zip' -OutFile $archive -UseBasicParsing
    $sha=(Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    if ($sha -ne '87FC4E1721DF37370EC67D0A244BB14F9BF804B0A95DF49E2AE7369D30EC72E8') {throw 'CalculiX archive checksum mismatch; do not execute this download.'}
    Expand-Archive -LiteralPath $archive -DestinationPath $cache -Force
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $solver) | Out-Null
    Copy-Item -LiteralPath (Join-Path $cache 'calculix_2.23_4win\ccx_static.exe') -Destination $solver
    Copy-Item -LiteralPath (Join-Path $cache 'calculix_2.23_4win\README_Install') -Destination (Join-Path $PSScriptRoot 'vendor\README_Install.txt')
}
if ($DevBuild -or !(Test-Path -LiteralPath 'frontend\dist\index.html')) {
    & (Join-Path $PSScriptRoot 'scripts\Install-Node.ps1')
    $localNode=Join-Path $PSScriptRoot '.runtime\tools\node-v24.19.0-win-x64'
    if (Test-Path -LiteralPath (Join-Path $localNode 'npm.cmd')) {$env:PATH=$localNode+';'+$env:PATH}
    if (!(Get-Command npm.cmd -ErrorAction SilentlyContinue)) {throw 'Project Node/npm installation failed.'}
    Push-Location frontend
    try { & npm.cmd ci; if ($LASTEXITCODE -ne 0) {throw 'npm ci failed'}; & npm.cmd run build; if ($LASTEXITCODE -ne 0) {throw 'Frontend build failed'} } finally {Pop-Location}
}
& $python -c "import numpy,scipy,gmsh,fastapi,h5py,reportlab,playwright,imageio_ffmpeg; print('Python dependencies ready; MP4 encoder: '+imageio_ffmpeg.get_ffmpeg_exe())"
if ($LASTEXITCODE -ne 0) {throw 'Dependency smoke check failed.'}
& $python -c "import os; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(channel=os.environ.get('STUDIO_VIDEO_BROWSER','msedge'),headless=True); b.close(); p.stop(); print('Background video browser ready.')"
if ($LASTEXITCODE -ne 0) {throw 'Video export requires Microsoft Edge, or an installed Playwright browser channel selected by STUDIO_VIDEO_BROWSER.'}
& $python scripts/build_manifest.py
if ($LASTEXITCODE -ne 0) {throw 'Build manifest generation failed.'}
Write-Output 'Installation complete. Double-click Start.bat.'
if (!(Test-Path -LiteralPath 'local-settings.json')) {Copy-Item -LiteralPath 'local-settings.example.json' -Destination 'local-settings.json'}
if ($GPU) { & (Join-Path $PSScriptRoot 'Install-GPU.ps1') }
