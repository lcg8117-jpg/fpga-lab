param([string]$Python='')
& (Join-Path $PSScriptRoot 'Launch.ps1') -Variant 1 -Python $Python
