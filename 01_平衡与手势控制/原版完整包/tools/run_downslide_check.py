"""Check down-slide STOP at optical-register boundary using the real decoder.

Full system UART/ADC/PWM is real RTL. The reader model injects completed 0x43/
0x44 sample pairs; physical I2C timing is outside this test and is unchanged.
"""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,time,xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
project=ROOT/'work/project'
stage=Path('D:/Codex_Scratch')/('ADB1_MOTION_QUEUE_DOWNSTOP_'+str(time.time_ns()))
stage.mkdir()
bound={}
for p in (project/'src').iterdir():
    if p.is_file():
        shutil.copy2(p,stage/p.name)
        bound[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
header=(stage/'forest_d1_gesture_reader.v').read_text('utf-8')
header=header[:header.index(');')+2]
model=header+r'''
 reg feed=0;
 initial begin raw43=0;raw44=0;end
 assign scl=1'bz;assign sda=1'bz;assign busy=!ready;
 forest_d1_gesture_decode decoder(.clk(clk),.rst_n(rst_n&&!restart),
  .flush(flush),.sample_valid(feed),.motion_inhibit(motion_inhibit),.raw43(raw43),.raw44(raw44),.map_flags(map_flags),
  .event_valid(event_valid),.event_code(event_code),.event_seq(event_seq),
  .suppressed_count(suppressed_count),.decode_reason(decode_reason),.diagnostic_state(diagnostic_state),.armed());
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin ready<=0;error_code<=0;error_count<=0;id0<=8'h20;id1<=8'h76;init_index<=218;poll_seq<=0;end
  else begin ready<=!restart;if(feed)poll_seq<=poll_seq+1;end
 end
endmodule
'''
(stage/'reader_boundary_model.v').write_text(model,'utf-8')
text=(ROOT/'tests/fixtures/system_natural_provider.sv').read_text('utf-8')
text=text[:text.index(' // Compare after NBA')]
start=text.index(' wire ref_tx');end=text.index(' integer clocks=')
text=text[:start]+text[end:]
text=text.replace('session_replica_tb','downstop_tb')
text=text.replace('.FEATURE_THREE_PROFILES(1)','.FEATURE_THREE_PROFILES(0)').replace('.FEATURE_AUTORETURN(1)','.FEATURE_AUTORETURN(0)')
text=text.replace("32'h4a325531","32'h41444231").replace("32'h4a325503","32'h41444203").replace('.CAPABILITIES(31)','.CAPABILITIES(15)')
text=text.replace("32'h44188000","32'h441b0000").replace("32'h437b0000","32'h43820000")
text=text.replace(' && base.zero_valid && !base.capture_active','').replace(' && base.source_ready','')
text=text.replace('base.mode','dut.mode').replace('base.fault','dut.fault')
text+=r'''
 integer before_sequence,before_events;
 always @(posedge clk)begin
  clocks=clocks+1;#1;
  if(rst_n)begin if(pwmb)pwm_high=pwm_high+1;if(dut.done)done_checks=done_checks+1;end
 end
 task optical(input [7:0] a,input [7:0] b);begin
  @(negedge clk);dut.gesture_hardware.reader.raw43=a;dut.gesture_hardware.reader.raw44=b;
  dut.gesture_hardware.reader.feed=1;
  @(negedge clk);dut.gesture_hardware.reader.feed=0;repeat(12)@(negedge clk);
 end endtask
 task released;begin optical(0,0);optical(0,0);optical(0,0);end endtask
 task start_balance;begin
  fresh_zero();command(8'h12,0,0,0,0);wait(dut.mode==3);milliseconds(8);released();
 end endtask
 initial begin
  log_file=$fopen("gesture_commands.csv","w");
  repeat(20)@(negedge clk);rst_n=1;milliseconds(12);
  read_config(254,8,15);kit_write(1,0);
  start_balance();adc=514;milliseconds(12);
  check(pwm_high>0 && done_checks>0,"nonzero balance and actual PWM");
  optical(0,1);
  check(dut.gesture_code==9 && dut.run_session && dut.mode==3,"wave remains classified but no longer stops balance");
  check(dut.last_action_reason==5,"wave is not reported as STOP");
  released();optical(8,0);
  check(dut.gesture_code==4 && !dut.run_session && {bn1,bn2,pwmb}==0,"down-slide stops balance and gates real motor pins");
  check(dut.last_action_reason==6 && dut.fault==0,"down-slide publishes STOP reason without ESTOP fault");
  milliseconds(8);check(!dut.run_session,"no automatic restart after down-slide");
  released();command(8'h31,0,0,0,0);milliseconds(5);
  check(dut.mode==5 && dut.run_session,"original single SWM4 still starts explicitly");
  released();optical(0,1);
  check(dut.mode==5 && dut.run_session,"wave no longer stops swing");
  released();optical(8,0);
  check(!dut.run_session && {bn1,bn2,pwmb}==0,"down-slide cancels swing and gates motor pins");
  milliseconds(8);released();optical(0,1);
  check(!dut.run_session,"wave while stopped cannot start motor");
  released();optical(8,0);check(!dut.run_session,"down-slide while stopped stays stopped");
  start_balance();released();optical(1,0);
  check(dut.motion_session && dut.motion_state==2,"right-slide keeps original continuous motion");
  released();before_sequence=dut.gesture_sequence;optical(8,0);
  check(dut.run_session && dut.gesture_sequence==before_sequence && dut.decode_reason==4,"down-slide is inhibited during command interval");
  command(8'h10,0,0,0,0);milliseconds(2);
  check(!dut.run_session && {bn1,bn2,pwmb}==0,"serial STOP remains immediate during optical interval");
  start_balance();released();optical(8'h40,0);
  check(dut.motion_session && dut.motion_state==1,"clockwise keeps original single-turn motion");
  check(dut.motion_guard_active,"action starts optical guard immediately");
  released();before_sequence=dut.gesture_sequence;before_events=dut.motion_events;
  optical(8'h02,0);
  check(dut.gesture_sequence==before_sequence && dut.motion_events==before_events && dut.decode_reason==4 && !dut.gesture_motion_valid,"left blocked before optical event and planner request");
  // No release is required to reach safe down-slide STOP after a blocked hand.
  optical(8'h08,0);
  check(dut.run_session && dut.gesture_sequence==before_sequence && dut.decode_reason==4,"down-slide after circle is blocked before event creation");
  command(8'h10,0,0,0,0);milliseconds(2);
  milliseconds(2);start_balance();released();optical(8'h40,0);
  command(8'h10,0,0,0,0);milliseconds(2);
  check(!dut.run_session && {bn1,bn2,pwmb}==0,"serial STOP remains effective");
  start_balance();released();optical(8'h80,0);
  check(dut.motion_session && dut.motion_state==1,"counterclockwise keeps original single-turn motion");
  @(negedge clk);keys[3]=0;#1;check({bn1,bn2,pwmb}==0,"raw ESTOP unchanged");
  $display("PASS DOWNSTOP_BOUNDARY clocks=%0d checks=%0d replies=%0d PWM=%0d no_internal_force=1",clocks,checks,reply_count,pwm_high);
  $fclose(log_file);$finish;
 end
 initial begin #1500000000;$fatal(1,"Down-stop test timeout mode=%0d fault=%h",dut.mode,dut.fault);end
endmodule
'''
(stage/'downstop_tb.sv').write_text(text,'utf-8')
files=[]
for e in ET.parse(project/'forest_d1_v1.gprj').findall('.//File'):
    if e.get('enable')=='1' and e.get('type')=='file.verilog':
        name=Path(e.get('path')).name
        files.append('reader_boundary_model.v' if name=='forest_d1_gesture_reader.v' else name)
sim=Path('D:/modeltech64_10.4/win64')
temporary=stage/'temp';temporary.mkdir();(temporary/'Temp').mkdir()
env=dict(os.environ);env.update(TEMP=str(temporary),TMP=str(temporary),TMPDIR=str(temporary))
checks=[]
for name,cmd in [('library',[sim/'vlib.exe','work']),('compile',[sim/'vlog.exe','-sv','+incdir+.',*files,'downstop_tb.sv']),('simulate',[sim/'vsim.exe','-c','-onfinish','exit','work.downstop_tb','-do','onerror {quit -code 2};run -all;quit -code 0'])]:
    r=subprocess.run([str(x) for x in cmd],cwd=stage,env=env,capture_output=True,text=True,errors='replace',timeout=300,creationflags=subprocess.CREATE_NO_WINDOW)
    output=r.stdout+r.stderr
    (ROOT/'reports'/('downstop_'+name+'.log')).write_text(output,'utf-8')
    ok=r.returncode==0 and '** Error:' not in output and '** Fatal:' not in output and (name!='simulate' or 'PASS DOWNSTOP_BOUNDARY' in output)
    checks.append(dict(name=name,passed=ok));print(name,ok,output[-1800:],flush=True)
    if not ok:raise RuntimeError('Down-slide boundary test failed; inspect log')
assert all(hashlib.sha256((project/'src'/n).read_bytes()).hexdigest()==h for n,h in bound.items())
result=dict(passed=True,checks=checks,stage=str(stage),source_sha256=bound,hardware_actions=0,
            scope='Real unchanged optical decoder and full system; completed register samples supplied by reader model. I2C timing excluded. No hierarchical force of DUT signals.')
(ROOT/'reports/DOWNSTOP_BOUNDARY.json').write_text(json.dumps(result,indent=2)+'\n','utf-8')
