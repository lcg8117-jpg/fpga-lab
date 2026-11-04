$ErrorActionPreference='Stop'
$case=$PSScriptRoot
$proof=[ordered]@{status='RUNNING';stage='NATIVE_WLF_EXTRACTION';hardware_actions=0}
try{
 $extract=Start-Process -FilePath 'E:/Moildesim/win64/vsim.exe' -ArgumentList @('-c','-view','results/word_font2.wlf','-do','verify_native_bulk.do') -WorkingDirectory $case -WindowStyle Hidden -RedirectStandardOutput "$case/logs/extract_bulk.stdout.log" -RedirectStandardError "$case/logs/extract_bulk.stderr.log" -PassThru
 if(-not $extract.WaitForExit(240000)){& taskkill /PID $extract.Id /T /F | Out-Null;throw 'Own WLF reader timed out'}
 if($extract.ExitCode -ne 0 -or -not (Select-String -LiteralPath "$case/logs/extract_bulk.stdout.log" -Pattern 'NATIVE_C6_FONT2_BULK_48000_ROWS_PASS' -Quiet)){throw 'Native extraction failed'}
 $proof.stage='MATLAB_INDEPENDENT_CROSSCHECK'
 $proof | ConvertTo-Json | Set-Content -LiteralPath "$case/results/FINAL_CHECK.json" -Encoding utf8NoBOM
 $matlab=Start-Process -FilePath 'D:/Program Files/MATLAB/R2025b/bin/matlab.exe' -ArgumentList @('-batch',"run('D:/C6_ClosedLoopFix_20261030/EasyStart_Font2/finish_verification.m')") -WorkingDirectory $case -WindowStyle Hidden -RedirectStandardOutput "$case/logs/verify.stdout.log" -RedirectStandardError "$case/logs/verify.stderr.log" -PassThru
 if(-not $matlab.WaitForExit(180000)){& taskkill /PID $matlab.Id /T /F | Out-Null;throw 'Own MATLAB verifier timed out'}
 if($matlab.ExitCode -ne 0 -or -not(Select-String -LiteralPath "$case/logs/verify.stdout.log" -Pattern 'C6_FINAL_FONT_EXPORTS_AND_ORACLE_PASS' -Quiet)){throw 'Independent MATLAB verification failed'}
 $proof.stage='FINAL_FONT_DIGITAL_REGRESSION'
 $proof | ConvertTo-Json | Set-Content -LiteralPath "$case/results/FINAL_CHECK.json" -Encoding utf8NoBOM
 $folder=Join-Path (Split-Path $case -Parent) 'Regression/tracked_EasyStart_Font3'
 $regression=Start-Process -FilePath 'E:/Moildesim/win64/vsim.exe' -ArgumentList @('-modelsimini','D:/C6_ClosedLoopFix_20261030/Regression/modelsim.ini','-c','-t','1ps','-wlf','none.wlf','+COUNT=508','work.c6_reference_tb','-do','D:/C6_ClosedLoopFix_20261030/Regression/run.do','-l','run.log') -WorkingDirectory $folder -WindowStyle Hidden -RedirectStandardOutput "$folder/stdout.log" -RedirectStandardError "$folder/stderr.log" -PassThru
 if(-not $regression.WaitForExit(120000)){& taskkill /PID $regression.Id /T /F | Out-Null;throw 'Own font HDL case timed out'}
 if($regression.ExitCode -ne 0 -or -not(Select-String -LiteralPath "$folder/stdout.log" -Pattern 'PASS C6_TRACKED_REFERENCE' -Quiet)){throw 'Final font HDL regression failed'}
 $proof.status='PASS';$proof.stage='COMPLETE';$proof.signedness_conversion='Unsigned9-bit index retained; signed32-bit feedback and signed16-bit correction decoded explicitly'
}catch{$proof.status='FAIL';$proof.error=$_.Exception.Message}
finally{$proof | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$case/results/FINAL_CHECK.json" -Encoding utf8NoBOM}
