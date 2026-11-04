param([string]$Python = '')
$ErrorActionPreference = 'Stop'
if (-not $Python) { $Python = Join-Path $PSScriptRoot 'runtime/pythonw.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Bundled Python is missing. Extract the complete ZIP first.' }
$env:TCL_LIBRARY = Join-Path $PSScriptRoot 'runtime/tcl/tcl8.6'
$env:TK_LIBRARY = Join-Path $PSScriptRoot 'runtime/tcl/tk8.6'
$logs = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$entry = Join-Path $PSScriptRoot 'run_host.py'
$arguments = @('-E','-s','-S','-B','-X','utf8',('"' + $entry + '"'))
$process = Start-Process -FilePath $Python -ArgumentList $arguments -WorkingDirectory $PSScriptRoot -WindowStyle Normal -RedirectStandardOutput (Join-Path $logs 'startup_stdout.log') -RedirectStandardError (Join-Path $logs 'startup_stderr.log') -PassThru
$process.Id | Set-Content -LiteralPath (Join-Path $logs 'last_window_pid.txt') -Encoding ascii
Start-Sleep -Milliseconds 900
$process.Refresh()
if ($process.HasExited -and $process.ExitCode -ne 0) {
    $detail = Get-Content -LiteralPath (Join-Path $logs 'startup_stderr.log') -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
    throw ('V4 startup failed. ' + $detail)
}
