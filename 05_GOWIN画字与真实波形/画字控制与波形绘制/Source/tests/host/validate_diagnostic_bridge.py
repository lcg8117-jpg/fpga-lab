"""Decode actual RTL diagnostic packets with the delivered Python Host codec."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'host'))
from protocol import decode,crc16,DEFAULT_GAINS,FIRMWARE,SCHEMA


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workroot',required=True,type=Path)
    parser.add_argument('--simulator',type=Path,default=Path('E:/Moildesim/win64'))
    args=parser.parse_args();work=args.workroot.resolve()
    assert str(work).isascii() and not work.exists();work.mkdir(parents=True)
    sources=[ROOT/'common/rtl/forest_d1_diagnostics.v',ROOT/'tests/host/diagnostic_codec_tb.sv',
             ROOT/'host/protocol.py',Path(__file__).resolve()]
    hashes={path.relative_to(ROOT).as_posix():hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    for path in sources[:2]:shutil.copy2(path,work/path.name)
    logs=[]
    def call(name,args,marker=None):
        log=work/(name+'.log')
        with log.open('wb') as output:
            run=subprocess.run([str(options.simulator/args[0]),*args[1:]],cwd=work,stdout=output,
                               stderr=subprocess.STDOUT,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
        text=log.read_text(errors='replace')
        assert run.returncode==0 and '** Error:' not in text and '** Fatal:' not in text and (marker is None or marker in text),text[-5000:]
        logs.append(dict(name=name,sha256=hashlib.sha256(log.read_bytes()).hexdigest(),passed=True))
    options=args
    call('vlib',['vlib.exe','work'])
    call('rtl',['vlog.exe','forest_d1_diagnostics.v'])
    call('tb',['vlog.exe','-sv','diagnostic_codec_tb.sv'])
    call('sim',['vsim.exe','-c','-onfinish','stop','work.diagnostic_codec_tb','-do','onerror {quit -code 2}; run -all; quit -code 0'],'PASS DIAGNOSTIC_CODEC_BRIDGE')
    checks=0;packets=[]
    for variant in (1,2,3):
        raw=bytearray(int((work/f'packet_v{variant}.hex').read_text().strip(),16).to_bytes(256,'little'))
        # The unchanged serial link appends CRC after the observed module payload.
        struct.pack_into('<H',raw,254,crc16(raw[:254]));item=decode(raw)
        expected=dict(variant=variant,firmware_id=FIRMWARE[variant],schema=SCHEMA,sequence=1,layout=1,
            board_ms=123500,last_completed_epoch=1000,last_completed_ms=123445,kit=1,zero_valid=1,
            delta_q4=-120,physical_encoder_raw=-2147483648,invalid_transitions=29,minimum_edge_clocks=30,
            physical_counts_per_rev=1040,forest_counts_per_rev=1040,measured_velocity=-1040,
            reference_velocity=520,tracking_error=-910,settle_ticks=45,stop_reason=11,
            adoption_event_seq=1,adoption_event_ms=123445,adoption_control_epoch=1000,
            adoption_feature_epoch=994,adoption_reason=1,last_adaptive_used=1,last_freeze=10,
            learning_code=9,learning_event_seq=10,learning_feature_epoch=999,learning_event_ms=123500,
            learning_reason=10,center_before=8,center_after=4,learning_phase=3,window_samples=0,
            cooldown_windows=4,reference_peak=30,window_peak=40,reference_energy=25600,
            window_energy=6400,event_samples=64,candidate_feature_epoch=999,candidate_revision=4,
            adopted_feature_epoch=994,stored_revision=5,active_revision=4,adoption_count=200,
            swing_finished_ms=123500,handoff_ms=123500,source_alpha=-100,source_alpha_rate=200,
            source_arm_rate=-300,source_capture_flags=0x57)
        for name,value in expected.items():
            assert item[name]==value,(name,item[name],value);checks+=1
        assert item['adoption_before_gains']==DEFAULT_GAINS
        assert item['adoption_after_gains']==(48.6171875,0,258.84375,25,0,610),item['adoption_after_gains']
        checks+=2;packets.append(dict(variant=variant,sha256=hashlib.sha256(raw).hexdigest()))
        shutil.copy2(work/f'packet_v{variant}.hex',ROOT/'tests/host/results'/f'packet_v{variant}.hex')
    assert hashes=={path.relative_to(ROOT).as_posix():hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    result=dict(passed=True,source_sha256=hashes,checks=checks,variants=packets,logs=logs,
                hardware_access=False,actual_RTL_packet=True,CRC_added_as_transport_does=True)
    out=ROOT/'tests/host/results/DIAGNOSTIC_CODEC_BRIDGE.json';out.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    for log in work.glob('*.log'):shutil.copy2(log,ROOT/'tests/host/results'/('bridge_'+log.name))
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
