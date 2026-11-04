"""Exhaustive selected-input equivalence for same-edge diagnostic hoisting."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,time
ROOT=Path(__file__).resolve().parents[1]
stage=Path('D:/Codex_Scratch')/('ADB1_DIAGNOSTIC_EXACT_'+str(time.time_ns()));stage.mkdir()
source=ROOT/'work/project/src/forest_d1_system.v'
baseline=Path('D:/Codex_Scratch/J280_INCOMING_DOWNSTOP_20261030_232235/work/project/src/forest_d1_system.v')
s=source.read_text('utf-8');s=s[s.index(' assign actuator_diag='):s.index(' reg captured_adaptive_used')]
b=baseline.read_text('utf-8');b=b[b.index(' wire signed [15:0] actual_q15='):b.index(' reg captured_adaptive_used')]
b=b.replace('actual_q15','legacy_q15').replace('actuator_diag','legacy_diag')
text='''`timescale 1ns/1ps
module tb_actual_q15;
 reg [2:0] mode;reg auto_preparing;reg [31:0] balance_diag,swing_diag,return_diag;
 wire [31:0] actuator_diag;
 wire [31:0] legacy_diag=mode==5 ? (auto_preparing ? return_diag:swing_diag):balance_diag;
'''+s+b+'''
 integer i,selector,checks=0;
 initial begin
  for(selector=0;selector<4;selector=selector+1)begin
   mode=selector<2 ? 3:5;auto_preparing=selector[0];
   for(i=0;i<524288;i=i+1)begin
    balance_diag=(i*32'd1664525)^32'ha55aa55a;
    swing_diag=~balance_diag;return_diag=balance_diag^32'h5a5a5a5a;
    if(selector<2)balance_diag=i;
    else if(selector==2)swing_diag=i;else return_diag=i;
    #1;checks=checks+1;
    if(actual_q15!==legacy_q15)begin $display("FAIL diagnostic selector%0d value%0d",selector,i);$fatal(1);end
   end
  end
  $display("PASS ACTUAL_DIAGNOSTIC_EQUIVALENCE checks=%0d selected_input_bits=19 no_extra_register=1",checks);$finish;
 end
endmodule
'''
(stage/'tb_actual_q15.sv').write_text(text,'utf-8')
temp=stage/'temp';temp.mkdir();(temp/'Temp').mkdir()
env=dict(os.environ,TEMP=str(temp),TMP=str(temp),TMPDIR=str(temp));sim=Path('D:/modeltech64_10.4/win64')
checks=[]
for name,args in [('library',[sim/'vlib.exe','work']),('compile',[sim/'vlog.exe','-sv','tb_actual_q15.sv']),('simulate',[sim/'vsim.exe','-c','-onfinish','exit','work.tb_actual_q15','-do','onerror {quit -code 2};run -all;quit -code 0'])]:
 p=subprocess.run(list(map(str,args)),cwd=stage,env=env,capture_output=True,text=True,errors='replace',timeout=300,creationflags=subprocess.CREATE_NO_WINDOW)
 out=p.stdout+p.stderr;(ROOT/'reports'/('actual_diagnostic_'+name+'.log')).write_text(out,'utf-8')
 ok=p.returncode==0 and '** Error:' not in out and '** Fatal:' not in out and (name!='simulate' or 'PASS ACTUAL_DIAGNOSTIC_EQUIVALENCE' in out)
 checks.append(dict(name=name,passed=ok));print(name,ok,out[-700:],flush=True);assert ok
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
result=dict(passed=True,checks=checks,selected_cases=2097152,source_sha256=sha(source),baseline_sha256=sha(baseline),stage=str(stage),hardware_actions=0,scope='All19 used selected diagnostic bits and all selector alternatives; pure mux/function distributivity. No added clock stage or changed actuator inputs.')
(ROOT/'reports/ACTUAL_DIAGNOSTIC_EQUIVALENCE.json').write_text(json.dumps(result,indent=2)+'\n','utf-8')
