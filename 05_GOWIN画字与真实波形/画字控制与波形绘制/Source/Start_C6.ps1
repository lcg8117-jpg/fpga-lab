$ErrorActionPreference='Stop'
Push-Location -LiteralPath $PSScriptRoot
try { & python -B host/app.py --variant 5 }
finally { Pop-Location }
