param([string]$Version='24.19.0')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$root=Split-Path -Parent $PSScriptRoot
$name="node-v$Version-win-x64"
$destination=Join-Path $root ".runtime\tools\$name"
if (!(Test-Path -LiteralPath (Join-Path $destination 'npm.cmd'))) {
    $cache=Join-Path $root '.runtime\downloads'
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $archive=Join-Path $cache "$name.zip"
    $base="https://nodejs.org/dist/v$Version"
    $checksums=(Invoke-WebRequest "$base/SHASUMS256.txt" -UseBasicParsing).Content
    $entry=($checksums -split "`n" | Where-Object {$_ -match "\s+$([regex]::Escape($name)).zip\s*$"})
    if (@($entry).Count -ne 1) {throw 'Official Node checksum entry not found.'}
    $expected=($entry -split '\s+')[0]
    if (!(Test-Path -LiteralPath $archive)) {Invoke-WebRequest "$base/$name.zip" -OutFile $archive -UseBasicParsing}
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) {throw 'Node archive checksum mismatch.'}
    Expand-Archive -LiteralPath $archive -DestinationPath (Split-Path -Parent $destination) -Force
}
$env:PATH=$destination+';'+$env:PATH
& (Join-Path $destination 'node.exe') --version
if ($LASTEXITCODE -ne 0) {throw 'Local Node smoke check failed.'}
