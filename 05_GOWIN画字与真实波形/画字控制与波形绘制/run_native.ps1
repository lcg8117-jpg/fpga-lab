$ErrorActionPreference='Stop'
$taskRoot=$PSScriptRoot
$arguments=@("$taskRoot/Source/tools/build_native.py",'--project',"$taskRoot/Source/V5/project/forest_d1_v5.gprj",'--compiler','D:/J280Build/toolchain/vendor/Gowin_V1.9.12.04_x64/IDE/bin/gw_sh.exe','--workroot',"$taskRoot/Native",'--status',"$taskRoot/reports/NATIVE_RESULT.json",'--usercode','56354336','--opt-goal','timing','--map-option','2','--place-option','3','--route-option','0','--maxfan','60')
$driver=Start-Process -FilePath 'E:/Python/python.exe' -ArgumentList $arguments -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput "$taskRoot/reports/native.stdout.log" -RedirectStandardError "$taskRoot/reports/native.stderr.log" -PassThru
$clock=[Diagnostics.Stopwatch]::StartNew()
$watch=[ordered]@{driver_pid=$driver.Id;time_limit_seconds=1200;total_bytes_limit=268435456;minimum_disk_free_bytes=10737418240;hardware_actions=0;status='RUNNING'}
while(-not $driver.HasExited){
 Start-Sleep -Seconds 10
 $driver.Refresh()
 $bytes=(Get-ChildItem -LiteralPath $taskRoot -File -Recurse | Measure-Object Length -Sum).Sum
 $free=(Get-PSDrive D).Free
 if($clock.Elapsed.TotalSeconds -gt 1200 -or $bytes -gt 268435456 -or $free -lt 10737418240){
  & taskkill /PID $driver.Id /T /F | Out-Null
  $watch.status='OWN_BUDGET_STOP';break
 }
 $watch.elapsed_seconds=[Math]::Round($clock.Elapsed.TotalSeconds,1);$watch.total_bytes=$bytes
 $watch | ConvertTo-Json | Set-Content -LiteralPath "$taskRoot/reports/NATIVE_WATCH.json" -Encoding utf8NoBOM
}
if($watch.status -eq 'RUNNING'){$watch.status='EXITED';$watch.exit_code=$driver.ExitCode}
$watch.elapsed_seconds=[Math]::Round($clock.Elapsed.TotalSeconds,1)
$watch | ConvertTo-Json | Set-Content -LiteralPath "$taskRoot/reports/NATIVE_WATCH.json" -Encoding utf8NoBOM
