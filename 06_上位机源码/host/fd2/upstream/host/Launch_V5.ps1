param([string]$Python='')
& (Join-Path $PSScriptRoot 'Launch.ps1') -Variant 5 -Python $Python
