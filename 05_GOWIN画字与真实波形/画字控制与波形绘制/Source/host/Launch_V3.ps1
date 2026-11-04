param([string]$Python='')
& (Join-Path $PSScriptRoot 'Launch.ps1') -Variant 3 -Python $Python
