$ErrorActionPreference = 'Stop'
$entry = Join-Path $PSScriptRoot 'swing_tune_host.py'
$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($launcher) { & $launcher.Source -3 $entry; exit $LASTEXITCODE }
$launcher = Get-Command python -ErrorAction SilentlyContinue
if ($launcher) { & $launcher.Source $entry; exit $LASTEXITCODE }
throw 'Install Python 3.10+ with Tcl/Tk, then run this script again.'
