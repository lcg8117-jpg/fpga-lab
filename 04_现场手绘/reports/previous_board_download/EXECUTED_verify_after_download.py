"""Complete verification after the successful single SRAM download; never program again."""
from pathlib import Path
import importlib.util,json,shutil,struct,sys,time,datetime
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('deploy_helper',HERE/'deploy_ds13.py')
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
d.EVID=HERE.parent/'outputs/J280_DS13_烧录记录_20261103_071329'
d.record=json.loads((d.EVID/'DEPLOYMENT_RESULT.json').read_text('utf-8'))
assert d.record['sram_attempts']==1 and d.record['after_jtag']==dict(id=0x281b,user=0x44533133)
assert next(c for c in d.record['calls'] if c['label']=='SRAM_PROGRAM')['exit_code']==0
assert not d.record['uart_requests']
old=d.module('before_k100',d.EVID/'before_k100_protocol.py')
rows=[r for _,r in old.FrameParser().feed((d.EVID/'BEFORE_STOPPED.bin').read_bytes())]
kits={r['kit'] for r in rows if r['kind'] in ('diagnostic','extension') and 'kit' in r}
assert len(kits)==1 and kits.issubset({0,1});oldkit=kits.pop()
shutil.copy2(d.EVID/'DEPLOYMENT_RESULT.json',d.EVID/'DEPLOYMENT_STAGE1_RESULT.json')
shutil.copy2(__file__,d.EVID/'EXECUTED_verify_after_download.py')
d.record['stage1_verification_issue']=d.record.pop('error')
d.record['verification_recovery']='K100 emits kit in extension frames, not diagnostic frames. Recovered exact previous kit from recorded pre-download bytes; no repeated SRAM download.'
d.record['previous_kit_from_extension']=oldkit
d.record['status']='POST_DOWNLOAD_VERIFICATION';d.save()
sys.path[:0]=[str(d.PACKAGE/'host'),str(d.PACKAGE/'host/vendor')]
import protocol as p
from hardware_mutex import SharedMutex
import serial

def execute():
    with SharedMutex():
        port=serial.Serial(port=None,baudrate=2000000,timeout=.03,write_timeout=.3,exclusive=True)
        port.dtr=False;port.rts=False;port.port='COM5';port.open()
        with port:
            d.capture(port,'REVERIFY_TRANSITION',.5,p.FrameParser)
            rows,proof=d.capture(port,'REVERIFY_STOPPED',1,p.FrameParser)
            d.stopped(rows,proof,p.FIRMWARE_ID,p.SCHEMA)
            parser=p.FrameParser();session=(time.time_ns()&0xffffffff) or 1;number=2000
            def request(opcode,group=0,payload=b''):
                nonlocal number
                assert opcode in p.READS or (opcode==p.WRITE_CONFIG and group==254 and payload==struct.pack('<BI',5,oldkit))
                number+=1;frame=p.request(opcode,number,group,payload,session=session,event=number)
                item=dict(opcode=opcode,request_id=number,raw_hex=frame.hex(),status='INTENT');d.record['uart_requests'].append(item);d.save()
                n=port.write(frame);item.update(status='WRITTEN',bytes=n);d.save();assert n==len(frame)
                raw=bytearray();end=time.monotonic()+3;ack=None
                while time.monotonic()<end and ack is None:
                    block=port.read(min(32768,max(1,port.in_waiting)));raw.extend(block)
                    for _,r in parser.feed(block):
                        assert r['identity_ok']
                        if r['kind']=='ack' and r['request_id']==number:
                            assert r['opcode']==opcode;ack=r
                (d.EVID/f'REQUEST_{number}_RX.bin').write_bytes(raw)
                assert parser.errors==0 and ack is not None
                item.update(status='ACK',ack=ack);d.save();assert ack['status']==0
                return ack
            request(p.HELLO);request(p.STATUS)
            gains=request(p.READ_GAINS,255);assert tuple(gains['stored_bits'])==p.S1_BITS
            configs={str(f):request(p.READ_CONFIG,254,bytes([f]))['config_read_value'] for f in (5,6,7,8)}
            if configs['5']!=oldkit:
                request(p.WRITE_CONFIG,254,struct.pack('<BI',5,oldkit))
                d.record['kit_selection_restored']=True;d.save()
                configs={str(f):request(p.READ_CONFIG,254,bytes([f]))['config_read_value'] for f in (5,6,7,8)}
            assert configs['5']==oldkit and configs['6']==(1040 if oldkit else 1080) and configs['7']==1040
            rows,proof=d.capture(port,'FINAL_STOPPED_S1',2.5,p.FrameParser)
            ts=d.stopped(rows,proof,p.FIRMWARE_ID,p.SCHEMA)
            assert all(tuple(r['manual_bits'])==p.S1_BITS and tuple(r['active_bits'])==p.S1_BITS for r in ts)
            d.record.update(status='COMPLETE_STOPPED',final_uart=proof,S1_readback=gains,S1_bits=[f'{v:08x}' for v in p.S1_BITS],
                configs=configs,manual_and_active_S1_exact=True,port_closed_after_verification=True,
                running_control_periods='Not measured on board: no ZERO/RUN. Verified in fresh compiled RTL tests.',
                uart_reads_and_previous_kit_only=True)
            d.save()
    print('DONE: one SRAM download; DS13 JTAG/UART and exact S1 verified; stopped; no ZERO/RUN or gains write',flush=True)

try:execute()
except BaseException as exc:
    d.record.update(status='POST_DOWNLOAD_VERIFICATION_FAILED',error=repr(exc));raise
finally:
    d.record['finished_local']=datetime.datetime.now().astimezone().isoformat();d.save()
