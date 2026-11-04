$ErrorActionPreference='Stop'
$taskRoot=$PSScriptRoot
$source=Join-Path $taskRoot 'Final'
$destination=Join-Path $taskRoot 'EasyStart_Font2'
if(-not(Test-Path -LiteralPath "$destination/data/Word_EasyStart.json")){throw 'Authored font input has not passed its guards'}
Copy-Item -LiteralPath "$source/rtl" -Destination "$destination/rtl" -Recurse
foreach($file in @('cosim_setup.tcl','modelsim.ini','start_word_cosim.do','word_closed_loop.sv','word_plant.m','run_word_cosim.m','export_word_results.m','export_native.do','verify_native.do')){Copy-Item -LiteralPath "$source/$file" -Destination "$destination/$file"}
foreach($file in @('start_word_cosim.do','run_word_cosim.m')){
 $path="$destination/$file";$content=(Get-Content -LiteralPath $path -Raw).Replace('4476','4478')
 Set-Content -LiteralPath $path -Value $content -Encoding utf8NoBOM
}
$path="$destination/export_word_results.m"
$content=(Get-Content -LiteralPath $path -Raw).Replace('final_lane','font2_lane').Replace('C6_Word_ClosedLoop_final','C6_Word_ClosedLoop_font2')
Set-Content -LiteralPath $path -Value $content -Encoding utf8NoBOM
@"
cd('D:/C6_ClosedLoopFix_20261030/EasyStart_Font2');
run_word_cosim(120,'font2');
export_word_results;
"@ | Set-Content -LiteralPath "$destination/run_case.m" -Encoding utf8NoBOM
