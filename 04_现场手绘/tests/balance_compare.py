"""Offline, dual whole-system manual-balance comparison against sealed ADB1.

Only balance_* tests/reports and tests/balance_run are written. Production RTL
is read, hashed, copied, and module-renamed; its numerical logic is untouched.
Run with python -B. No serial, programmer, or hardware APIs are used.
"""
from pathlib import Path
import argparse, hashlib, importlib.util, json, re, subprocess, sys, time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SEALED = Path('C:/Users/董晨阳/Documents/Codex/2026-11-02/j280-adb1-s1-adaptive-integrated-build/outputs/ADB1旧版_S1现场封存_20261103_0342/原版完整包/native_evidence/src')
PROBES = [
    ('reset_n', 0), ('control_rst_n', 0), ('tick_1ms', 0), ('uptime_ms', 0),
    ('raw', 0), ('clean', 0), ('filtered', 0), ('adc_batch_valid', 0),
    ('enc', 0), ('invalid', 0), ('mode', 0), ('fault', 0), ('zero_valid', 0),
    ('capture_active', 0), ('zero_q4', 0), ('encoder_zero', 0), ('run_session', 0),
    ('balance_permission', 0), ('actual_balance_permission', 0), ('mapping_valid', 0),
    ('source_rst_n', 0), ('source_ready', 0), ('motion_session', 0),
    ('mapped_encoder', 0), ('control_encoder', 0), ('mapping_fault', 0),
    ('frame_start', 0), ('angle_capture', 0), ('angle_valid', 0),
    ('mapped_angle', 0), ('raw_mapped_angle', 0), ('sample_en', 0),
    ('angle', 0), ('encoder_sample', 0), ('raw_guard_mean', 0),
    ('source_window_bad', 0), ('source_guard_mean', 0),
    ('fast_request', 0), ('fast_mapping_valid', 0), ('fast_mapped_angle', 0),
    ('fast_control_encoder', 0), ('fast_acquisition_fault', 0),
    ('compare_deadline_fault', 0), ('acquisition_fault', 0), ('control_fast_p', 0),
    ('busy', 0), ('done', 0), ('observation_start', 0),
    ('observation_angle', 0), ('observation_encoder', 0), ('observation_delta', 0),
    ('position_div', 0), ('fast_position_div', 0), ('target', 0),
    ('control_angle_d_epoch', 0), ('control_position_d_epoch', 0), ('control_position_p_epoch', 0),
    ('moto', 0), ('balance_pwm', 0), ('position_pwm', 0),
    ('angle_p', 0), ('angle_i', 0), ('angle_d', 0), ('position_p', 0), ('position_i', 0), ('position_d', 0),
    ('bias_bits', 0), ('angle_difference', 0), ('position_bias', 0), ('position_diff', 0),
    ('balance_diag', 0), ('actuator.duty_actual_q15', 0), ('actuator.motor_duty_prepared_q15', 0),
    ('core.original.sampled_encoder', 0), ('core.original.sampled_angle', 0),
    ('core.fast.position_error', 0), ('core.fast.angle_error', 0), ('core.fast.bias', 0),
    ('core.fast.held_ad', 0), ('core.fast.held_pd', 0),
    ('core.completed_gains[63:0]', 1), ('core.completed_gains[127:64]', 1), ('core.completed_gains[191:128]', 1),
    ('core.original.sample_en', 0), ('core.fast.sample_en', 0), ('core.original.done', 0), ('core.fast.done', 0),
    ('core.original.state', 0), ('core.fast.state', 0),
    ('core.original.sampled_clear', 0), ('core.original.sampled_prime', 0),
    ('core.original.last_position', 0), ('core.original.last_bias', 0),
    ('core.original.position_integral', 0), ('core.original.angle_integral', 0),
    ('core.base_seen', 0), ('core.fast_seen', 0),
]

def hashes(src):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(src.iterdir()) if p.is_file()}

def snapshot(src, dest, prefix):
    dest.mkdir(parents=True, exist_ok=True)
    files = list(src.glob('*.v')) + list(src.glob('*.vh'))
    names = set()
    for p in files:
        names.update(re.findall(r'\bmodule\s+(\w+)', p.read_text('utf-8')))
    pattern = re.compile(r'\b(' + '|'.join(sorted(names, key=len, reverse=True)) + r')\b')
    changed = {}
    for p in files:
        old = p.read_text('utf-8')
        new = pattern.sub(lambda m: prefix + m.group(0), old)
        # Verify that reversing ONLY module identifier prefixes restores text.
        reverse = re.sub(r'\b' + prefix + r'(' + '|'.join(names) + r')\b', lambda m: m.group(1), new)
        assert reverse == old, 'non-rename source change: ' + p.name
        (dest/p.name).write_text(new, 'utf-8')
        changed[p.name] = hashlib.sha256(new.encode('utf-8')).hexdigest()
    return changed

def wrapper():
    n = len(PROBES)
    text = f'''// Test only. Both physical systems see the identical inputs.
module balance_probe(input clk,rst_n,kit,zero_cmd,run_cmd,stop_cmd,
 input [9:0] adc,input ea,eb,
 output [63:0] ref_p[0:{n-1}],dut_p[0:{n-1}],
 output [2:0] ref_pins,dut_pins,output ref_adc_clk,dut_adc_clk,
 output [191:0] ref_bank,dut_bank,
 output signed [63:0] ref_wide,dut_wide,ref_target,dut_target);
 tri1 rscl,rsda,dscl,dsda;
 localparam [191:0] S1={{32'h441a270a,32'd0,32'h418d5c29,32'h435223d7,32'd0,32'h42571eb8}};
 ref_forest_d1_system #(.VARIANT(1),.FIRMWARE_ID(32'h41444231),.SCHEMA(32'h41445001),
  .PROTOCOL_VERSION(3),.PROTOCOL_VARIANT(16),.CAPABILITIES(32'h0000000f),
  .FEATURE_SWING(1),.FEATURE_THREE_PROFILES(0),.FEATURE_GESTURE(1),.FEATURE_MOTION(1),.FEATURE_ADAPTIVE(1),.FEATURE_AUTORETURN(0),
  .SWING_CAPTURE_ALPHA_Q20(32'sd274517),.SWING_CAPTURE_AD_Q20(32'sd3660218),
  .SWING_CAPTURE_THD_Q20(32'sd2562155),.SWING_HANDOFF_ENTER_Q4(16'd634),
  .STARTUP_CYCLES(100),.KEY_STABLE_CYCLES(4),.ZERO_SAMPLES(8)) reference(
  .clk_50m(clk),.rst_n(rst_n),.key_sw(4'b1111),.uart_rx(1'b1),.uart_tx(),
  .adc_clk(ref_adc_clk),.adc_oe_n(),.adc_data_in(adc),.adc_otr(1'b0),.enc1_a(ea),.enc1_b(eb),
  .AN1(),.AN2(),.PWMA(),.BN1(ref_pins[2]),.BN2(ref_pins[1]),.PWMB(ref_pins[0]),.gesture_scl(rscl),.gesture_sda(rsda));
 dut_forest_d1_system #(.VARIANT(5),.STARTUP_CYCLES(100),.KEY_STABLE_CYCLES(4),.ZERO_SAMPLES(8)) candidate(
  .clk_50m(clk),.rst_n(rst_n),.key_sw(4'b1111),.uart_rx(1'b1),.uart_tx(),
  .bt_rx(1'b1),.bt_state(1'b0),.bt_tx(),
  .adc_clk(dut_adc_clk),.adc_oe_n(),.adc_data_in(adc),.adc_otr(1'b0),.enc1_a(ea),.enc1_b(eb),
  .AN1(),.AN2(),.PWMA(),.BN1(dut_pins[2]),.BN2(dut_pins[1]),.PWMB(dut_pins[0]),.gesture_scl(dscl),.gesture_sda(dsda));
 // Only gateway outputs are bound. No controller, ADC, encoder, sampler,
 // safety, planner, filter, state, or actuator node is forced.
 always @* begin
'''
    for inst in ('reference', 'candidate'):
        for sig, val in [('gains_stored','S1'),('kit_ip570','kit'),('zero_pulse','zero_cmd'),('run_pulse','run_cmd'),('stop_pulse','stop_cmd'),('gesture_enable',"1'b0")]:
            text += f'  force {inst}.gateway.{sig}={val};\n'
    text += ' end\n'
    for i,(sig,_) in enumerate(PROBES):
        text += f' assign ref_p[{i}]=reference.{sig}; assign dut_p[{i}]=candidate.{sig};\n'
    text += ''' assign ref_bank=reference.stored_gains;assign dut_bank=candidate.stored_gains;
 assign ref_wide=reference.wide_encoder;assign dut_wide=candidate.wide_encoder;
 assign ref_target=reference.wide_target;assign dut_target=candidate.wide_target;
endmodule
'''
    return text

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--candidate',type=Path,default=ROOT/'V5/project/src');ap.add_argument('--label',default='ds13');args=ap.parse_args()
    assert re.fullmatch('[a-zA-Z0-9_]+',args.label)
    run=ROOT/'tests/balance_run'/args.label;run.mkdir(parents=True,exist_ok=True)
    report=ROOT/'reports'/('balance_'+args.label);report.parent.mkdir(exist_ok=True)
    before={'sealed':hashes(SEALED),'candidate':hashes(args.candidate)}
    state={'status':'BUILDING','passed':False,'hardware_actions':0,'candidate':str(args.candidate),'sealed':str(SEALED),
      'source_sha256':before,'test_scope':'No drawing; identical physical ADC/quadrature; full RTL at 50 MHz; both kits; STOP/restart/re-ZERO',
      'test_only_overrides':{'startup_cycles':100,'key_stable_cycles':4,'zero_samples':8,'gateway':['S1 gains','kit','ZERO/RUN/STOP pulses','gesture disabled']},
      'limitations':['Open-loop physical stimulus, not a mechanical plant or hardware stability test','Startup/zero capture shortened equally; all control/filter/PWM periods unchanged','No claim about drawing, adaptive operation, swing, external ADC timing or routed netlist equivalence']}
    def save():report.with_suffix('.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),'utf-8')
    save()
    try:
        state['renamed_snapshot_sha256']={'sealed':snapshot(SEALED,run/'reference','ref_'),'candidate':snapshot(args.candidate,run/'candidate','dut_')}
        (run/'balance_probe.v').write_text(wrapper(),'utf-8')
        header='#pragma once\n#include <cstdint>\n'+f'constexpr int NPROBES={len(PROBES)};\n'
        header+='static const char* names[]={'+','.join(json.dumps(x[0]) for x in PROBES)+'};\n'
        header+='static const int gated[]={'+','.join(str(x[1]) for x in PROBES)+'};\n'
        for i,(sig,_) in enumerate(PROBES):header+='constexpr int P_'+re.sub(r'\W+','_',sig).strip('_')+'='+str(i)+';\n'
        (run/'balance_probes.h').write_text(header,'utf-8')
        (run/'balance_driver.cpp').write_bytes((ROOT/'tests/balance_driver.cpp').read_bytes())
        spec=importlib.util.spec_from_file_location('balance_helper',ROOT/'tests/rtl_compile.py')
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        verilog=['-I'+str(run/'reference'),'-I'+str(run/'candidate'),str(run/'balance_probe.v')]
        verilog += [str(p) for sub in ['reference','candidate'] for p in sorted((run/sub).glob('*.v'))]
        start=time.monotonic();exe=helper.build(run,'balance_probe',verilog,'balance_driver.cpp')
        state['compile_seconds']=round(time.monotonic()-start,3);state['status']='RUNNING';save()
        start=time.monotonic()
        with report.with_suffix('.log').open('w',encoding='utf-8') as log:
            result=subprocess.run([str(exe)],cwd=run,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        state['run_seconds']=round(time.monotonic()-start,3);state['exit_code']=result.returncode
        state['production_source_unchanged']=before=={'sealed':hashes(SEALED),'candidate':hashes(args.candidate)}
        state['passed']=result.returncode==0 and state['production_source_unchanged']
        state['status']='PASS' if state['passed'] else 'FAIL'
        print(report.with_suffix('.log').read_text('utf-8'),flush=True)
    except Exception as exc:
        state['status']='ERROR';state['error']=str(exc);raise
    finally:
        state['test_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),ROOT/'tests/balance_driver.cpp',ROOT/'tests/rtl_compile.py']}
        save()
    raise SystemExit(0 if state['passed'] else 1)

if __name__=='__main__':main()
