"""Qualify real pin-level UART, ADC, encoder, safety and SWM4/motion entry."""
from pathlib import Path
import hashlib,json,os,re,shutil,subprocess,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]


def main():
    base=Path('D:/Codex_Scratch/ADB1_MOTION_QUEUE_NATURAL_001');stage=base;n=1
    while stage.exists():n+=1;stage=base.with_name(base.name+'_'+str(n))
    stage.mkdir(parents=True,exist_ok=False)
    provider=ROOT/'tests/fixtures/system_natural_provider.sv'
    text=provider.read_text();text=text[:text.index(' // Compare after NBA')]
    start=text.index(' wire ref_tx');end=text.index(' integer clocks=')
    text=text[:start]+text[end:]
    text=text.replace('session_replica_tb','natural_tb')
    text=text.replace('.FEATURE_THREE_PROFILES(1)','.FEATURE_THREE_PROFILES(0)').replace('.FEATURE_AUTORETURN(1)','.FEATURE_AUTORETURN(0)')
    text=text.replace("32'h4a325531","32'h41444231").replace("32'h4a325503","32'h41445001").replace('.CAPABILITIES(31)','.CAPABILITIES(15)')
    text=text.replace("32'h44188000","32'h441b0000").replace("32'h437b0000","32'h43820000")
    text=text.replace(' && base.zero_valid && !base.capture_active','').replace(' && base.source_ready','')
    text=text.replace('base.mode','dut.mode').replace('base.fault','dut.fault')
    text+=r'''
 always @(posedge clk)begin
  clocks=clocks+1;#1;
  if(rst_n)begin
   if(pwmb)pwm_high=pwm_high+1;
   if(dut.done)done_checks=done_checks+1;
   if(dut.sample_en)sample_checks=sample_checks+1;
   if(dut.tlm_valid)telemetry_checks=telemetry_checks+1;
  end
 end
 initial begin
  log_file=$fopen("natural_commands.csv","w");
  repeat(20)@(negedge clk);rst_n=1;milliseconds(12);
  read_config(254,8,15);read_config(254,9,32'h41445001);
  read_config(251,10,1000);
  command(8'h32,1,1,0,6);command(8'h30,0,1,1,3);
  command(8'h30,0,1,0,0);kit_write(1,0);
  fresh_zero();milliseconds(2);check(dut.control_fast_p && dut.compare_ready,"fast P primed naturally before RUN");
  command(8'h12,0,0,0,0);wait(dut.mode==3);
  for(i=0;i<12;i=i+1)begin @(negedge dut.done);@(negedge clk);adc=510+(i%5);edge_step(i%8<4);end
  check(pwm_high>0 && done_checks>=12,"nonzero actual PID and PWM from pin inputs");
  command(8'h22,0,0,0,0);command(8'h40,0,0,0,0);milliseconds(35);
  check(dut.motion_session && dut.motion_events>0,"legacy motion accepted through real UART");
  command(8'h44,0,0,0,0);command(8'h23,0,0,0,0);command(8'h10,0,0,0,0);
  milliseconds(2);check(!dut.run_session && {bn1,bn2,pwmb}==0,"STOP inhibits drive");
  command(8'h31,0,0,0,0);milliseconds(10);check(dut.mode==5,"single SWM4 entry");
  command(8'h10,0,0,0,0);milliseconds(2);check(!dut.run_session,"SWM4 cancel");
  fresh_zero();milliseconds(2);check(dut.control_fast_p && dut.compare_ready,"fast P primed naturally before RUN");
  command(8'h12,0,0,0,0);wait(dut.mode==3);adc=513;milliseconds(12);
  @(negedge clk);keys[3]=0;#1;check({bn1,bn2,pwmb}==0,"raw ESTOP immediately inhibits pins");
  milliseconds(2);check(dut.fault==3,"ESTOP latches fault");keys[3]=1;command(8'h13,0,0,0,0);
  fresh_zero();milliseconds(2);check(dut.control_fast_p && dut.compare_ready,"fast P primed naturally before RUN");
  command(8'h12,0,0,0,0);wait(dut.mode==3);milliseconds(510);
  check(!dut.run_session && {bn1,bn2,pwmb}==0,"real500ms watchdog stops remote owner");
  check(reply_count>=20 && control_packets>=10 && diagnostic_packets>0 && extension_packets>0,"real CRC checked ACK/T/D/E coverage");
  check(dut.invalid==0 && dut.rx_errors==0,"clean physical encoder and UART");
  check(!dut.compare_deadline_fault && !dut.fast_acquisition_fault,"no fast acquisition or compute overrun");
  $display("PASS NATURAL_SYSTEM clocks=%0d checks=%0d done=%0d pwm_high=%0d replies=%0d T=%0d D=%0d E=%0d no_force=1",clocks,checks,done_checks,pwm_high,reply_count,control_packets,diagnostic_packets,extension_packets);
  $fclose(log_file);$finish;
 end
 initial begin #1500000000;$fatal(1,"Natural system timeout mode=%0d fault=%h",dut.mode,dut.fault);end
endmodule
'''
    (stage/'natural_tb.sv').write_text(text,encoding='utf8')
    project=ROOT/'work/project';files=[]
    bound={}
    for p in (project/'src').iterdir():
        shutil.copyfile(p,stage/p.name);bound[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
    for e in ET.parse(project/'forest_d1_v1.gprj').findall('.//File'):
        if e.get('enable')=='1' and e.get('type')=='file.verilog':files.append(Path(e.get('path')).name)
    sim=Path('D:/modeltech64_10.4/win64');checks=[]
    commands=[('vlib',[sim/'vlib.exe','work']),('compile',[sim/'vlog.exe','-sv','+incdir+.',*files,'natural_tb.sv']),
              ('natural',[sim/'vsim.exe','-c','-onfinish','exit','work.natural_tb','-do','onerror {quit -code 2};run -all;quit -code 0'])]
    env=dict(os.environ);temporary=stage/'temp';temporary.mkdir();(temporary/'Temp').mkdir();env.update(TEMP=str(temporary),TMP=str(temporary),TMPDIR=str(temporary))
    for name,command in commands:
        result=subprocess.run([str(x) for x in command],cwd=stage,env=env,capture_output=True,text=True,errors="replace",timeout=600,creationflags=subprocess.CREATE_NO_WINDOW)
        output=result.stdout+result.stderr;(ROOT/'reports'/('natural_'+name+'.log')).write_text(output,encoding='utf8')
        ok=result.returncode==0 and '** Error:' not in output and '** Fatal:' not in output and (name!='natural' or 'PASS NATURAL_SYSTEM' in output)
        checks.append(dict(name=name,passed=ok));print(name,ok,output[-1800:],flush=True)
        if not ok:raise RuntimeError('Natural-input check failed; inspect reports/natural_'+name+'.log')
    assert all(hashlib.sha256((project/'src'/n).read_bytes()).hexdigest()==h for n,h in bound.items())
    (ROOT/'reports/NATURAL_TESTS.json').write_text(json.dumps(dict(passed=True,checks=checks,source_sha256=bound,stage=str(stage),source_unchanged=True,hardware_actions=0),indent=2),encoding='utf8')


if __name__=='__main__':main()
