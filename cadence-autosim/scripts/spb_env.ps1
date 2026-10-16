# Cadence Allegro X / OrCAD X (SPB) environment for scripted, headless use.
#
#   . .\scripts\spb_env.ps1                       # autodetect the install
#   . .\scripts\spb_env.ps1 -SpbRoot D:\apps\Cadence\SPB_24.1
#
# Dot-source it (note the leading ". ") so the variables land in your session;
# running it as a script leaves the child process with the environment and you
# with nothing.
#
# Why this is needed at all: the CLI tools (`bem2d`, `signoise`, `tlsim`,
# `allegro_batch`, `il_allegro`) abort with "Can't find Allegro env file" unless
# CDSROOT is set, and they look for `pcbenv` under HOME, which for a scripted run
# should be the tool's own data directory rather than the user profile.

param(
    [string]$SpbRoot = $env:SPBROOT,
    [string]$SpbHome = $env:SPB_HOME
)

if (-not $SpbRoot) {
    $candidates = @(
        'D:\tools\Cadence\SPB_24.1',
        'C:\Cadence\SPB_24.1',
        (Join-Path $env:USERPROFILE 'Cadence\SPB_24.1')
    )
    $SpbRoot = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $SpbRoot -or -not (Test-Path -LiteralPath $SpbRoot)) {
    throw "Cadence root not found - pass -SpbRoot <path to SPB_xx.y>"
}
if (-not $SpbHome) {
    $SpbHome = Join-Path (Split-Path -Parent $SpbRoot) 'SPB_Data'
}

$env:CDSROOT = $SpbRoot
$env:SPBROOT = $SpbRoot
$env:TELENV  = Join-Path $SpbRoot 'share\pcb\text\env'
$env:CDS_LIC_FILE = if ($env:CDS_LIC_FILE) { $env:CDS_LIC_FILE } else { '5280@localhost' }
$env:HOME    = $SpbHome

# PCB tools first, then the shared bin, then whatever was already on PATH.
$env:PATH = @(
    (Join-Path $SpbRoot 'tools\pcb\bin'),
    (Join-Path $SpbRoot 'tools\bin'),
    $env:PATH
) -join ';'

Write-Host "[spb-env] CDSROOT = $env:CDSROOT"
Write-Host "[spb-env] TELENV  = $env:TELENV  (exists: $(Test-Path -LiteralPath $env:TELENV))"
Write-Host "[spb-env] HOME    = $env:HOME    (exists: $(Test-Path -LiteralPath $env:HOME))"
foreach ($tool in 'bem2d.exe', 'signoise.exe', 'tlsim.exe', 'allegro_batch.exe') {
    $p = Join-Path $SpbRoot "tools\bin\$tool"
    if (Test-Path -LiteralPath $p) { Write-Host "[spb-env] found $tool" }
}
