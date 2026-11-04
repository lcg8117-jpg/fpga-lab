param(
    [Parameter(Mandatory=$true)][ValidateSet(1,2,3,4,5)][int]$Variant,
    [string]$Python = ''
)
$ErrorActionPreference = 'Stop'
if (-not $Python) { $Python = Join-Path (Split-Path -Parent $PSScriptRoot) 'runtime/python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Bundled runtime missing. Extract the complete release, including runtime and host.' }
Push-Location -LiteralPath $PSScriptRoot
try {
    & $Python -B -X utf8 (Join-Path $PSScriptRoot 'app.py') --variant $Variant
    if ($LASTEXITCODE -ne 0) { throw "Host exited with code $LASTEXITCODE" }
} finally { Pop-Location }
