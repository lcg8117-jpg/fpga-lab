$ErrorActionPreference='Stop'
$reg=$PSScriptRoot
$jobs=@(
 @{name='buffer';top='c6_buffer_tb';extra=@()},
 @{name='follow';top='c6_follow_tb';extra=@()},
 @{name='fixed';top='c5_fixed_tb';extra=@()},
 @{name='chain';top='c5_fixed_tb';extra=@('-gCHAIN_TEST=1')},
 @{name='GOWIN';top='c4_reference_tb';extra=@('+COUNT=438')},
 @{name='EasyStart';top='c4_reference_tb';extra=@('+COUNT=478')},
 @{name='tracked_GOWIN';top='c6_reference_tb';extra=@('+COUNT=438')},
 @{name='tracked_EasyStart';top='c6_reference_tb';extra=@('+COUNT=478')},
 @{name='guards';top='c4_guard_tb';extra=@('+COUNT=438')},
 @{name='system';top='c4_system_tb';extra=@()}
)
$result=[ordered]@{status='RUNNING';cases=@();hardware_actions=0;scope='Actual source ModelSim regression; ideal/external stimuli for digital tests, not plant validation'}
try{
 foreach($job in $jobs){
  $folder=Join-Path $reg $job.name
  $ini=(Join-Path $reg 'modelsim.ini').Replace('\','/')
  $script=(Join-Path $reg 'run.do').Replace('\','/')
  $args=@('-modelsimini',$ini,'-c','-t','1ps','-wlf','none.wlf')+$job.extra+@(('work.'+$job.top),'-do',$script,'-l','run.log')
  $process=Start-Process -FilePath 'E:\Moildesim\win64\vsim.exe' -ArgumentList $args -WorkingDirectory $folder -WindowStyle Hidden -RedirectStandardOutput (Join-Path $folder 'stdout.log') -RedirectStandardError (Join-Path $folder 'stderr.log') -PassThru
  if(-not $process.WaitForExit(240000)){
   & taskkill /PID $process.Id /T /F | Out-Null
   throw "Own HDL case timeout: $($job.name)"
  }
  $text=(Get-Content -LiteralPath (Join-Path $folder 'stdout.log') -Raw)+(Get-Content -LiteralPath (Join-Path $folder 'stderr.log') -Raw)
  $pass=@($text -split "`n" | Where-Object {$_ -match 'PASS '})
  if($process.ExitCode -ne 0 -or $text -match '\*\* (Error|Fatal):' -or $pass.Count -eq 0){throw "HDL case failed: $($job.name)"}
  $result.cases+=@{name=$job.name;pass=$pass;exit_code=$process.ExitCode}
  $result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $reg 'RESULT.json') -Encoding utf8NoBOM
 }
 $result.status='PASS'
}catch{
 $result.status='FAIL';$result.error=$_.Exception.Message
}finally{
 $result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $reg 'RESULT.json') -Encoding utf8NoBOM
}
