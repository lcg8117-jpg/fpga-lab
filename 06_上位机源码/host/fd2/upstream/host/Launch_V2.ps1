param([string]$Python='')
& (Join-Path $PSScriptRoot 'Launch.ps1') -Variant 2 -Python $Python
