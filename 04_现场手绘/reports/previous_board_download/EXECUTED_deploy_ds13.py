"""One user-authorized DS13 SRAM download; passive stop check and read-only S1 verification."""
from pathlib import Path
import datetime,hashlib,importlib.util,json,re,shutil,struct,subprocess,sys,time

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'outputs/J280_DS13_S1平衡还原_现场手绘修复_20261103'
CLI=Path('D:/Gowin/Gowin_V1.9.12.03_x64/Programmer/bin/programmer_cli.exe')
CLI_SHA='6cadcfe296ad8d58a2b9f23e855d39c3d18d59b61bbd4de68594139727ca5b93'
FS_SHA=json.loads((PACKAGE/'RELEASE.json').read_text('utf-8'))['bitstream_sha256']
OLD_PROTOCOL=Path('C:/Users/董晨阳/Documents/Codex/2026-11-02/j280-adb1-fastp-load-adaptation/outputs/ADB1_K100_配套上位机_20261103/host/protocol.py')
STAMP=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
EVID=ROOT/'outputs'/('J280_DS13_烧录记录_'+STAMP)
IMAGE=Path('D:/Codex_Scratch/J280_LIVE_S1_FIX_DS13_20261103_CHAT/deploy')/STAMP/'draw_s1.fs'
record=dict(status='PREPARED',sram_attempts=0,nonvolatile_flash_written=False,ZERO_sent=False,RUN_sent=False,
            motion_sent=False,SET_GAINS_sent=False,hardware_actions=0,uart_requests=[],calls=[],
            authorization='User explicitly repeated 直接烧录 after being informed the K100 window had appeared; switch the stopped board from K100 to qualified DS13, without ZERO/RUN or closing other processes.',
            physical_tracking_verified=False,package=str(PACKAGE),firmware_sha256=FS_SHA)

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save():
    dest=EVID/'DEPLOYMENT_RESULT.json';tmp=dest.with_suffix('.tmp')
    tmp.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(dest)
def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def capture(port,label,seconds,parser_type):
    parser=parser_type();raw=bytearray();rows=[];end=time.monotonic()+seconds
    while time.monotonic()<end:
        block=port.read(min(32768,max(1,port.in_waiting)));raw.extend(block)
        rows.extend(r for _,r in parser.feed(block))
        assert len(raw)<4*1024*1024
    (EVID/(label+'.bin')).write_bytes(raw)
    errors=getattr(parser,'errors',getattr(parser,'crc_errors',0)+getattr(parser,'format_errors',0))
    proof=dict(frames=len(rows),bytes=len(raw),errors=errors,sha256=hashlib.sha256(raw).hexdigest(),
               latest={kind:next((r for r in reversed(rows) if r['kind']==kind),None) for kind in ('telemetry','diagnostic','ack')})
    dump(EVID/(label+'.json'),proof);return rows,proof
def stopped(rows,proof,fw,schema,variant=5):
    ts=[r for r in rows if r['kind']=='telemetry'];assert len(ts)>=10 and proof['errors']==0,'Insufficient clean telemetry'
    assert ts[-1]['sequence']>ts[0]['sequence'] and ts[-1]['board_ms']>ts[0]['board_ms'],'Not fresh'
    assert all(r['firmware_id']==fw and r['schema']==schema and r['variant']==variant for r in ts),'Identity mismatch'
    assert all(r['mode'] in (0,1,4) and not (r['flags']&6) and r['actual_applied_q15']==0 for r in ts),'Board not stopped'
    return ts
def cli(label,args,timeout=30):
    row=dict(label=label,args=args,status='INTENT');record['calls'].append(row);save()
    result=subprocess.run([str(CLI),*args],capture_output=True,timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW)
    raw=result.stdout+result.stderr;(EVID/(label+'.log')).write_bytes(raw)
    row.update(status='RETURNED',exit_code=result.returncode,log_sha256=sha(EVID/(label+'.log')));save()
    assert result.returncode==0,(label,result.returncode)
    return raw.decode('utf-8',errors='replace')
def jtag(value):
    return {key:int(re.search(pattern,value,re.I).group(1),16) for key,pattern in
            [('id',r'ID Code is:\s*0x([0-9a-f]+)'),('user',r'User Code is:\s*0x([0-9a-f]+)')]}
def execute():
    assert sha(CLI)==CLI_SHA and sha(PACKAGE/'firmware/J280_DS13_S1_LIVE.fs')==FS_SHA
    checked=subprocess.run([str(PACKAGE/'runtime/python.exe'),'-B',str(PACKAGE/'verify_delivery.py')],capture_output=True,timeout=120)
    assert checked.returncode==0,checked.stdout+checked.stderr
    EVID.mkdir();(EVID/'DELIVERY_CHECK.log').write_bytes(checked.stdout+checked.stderr)
    shutil.copy2(__file__,EVID/'EXECUTED_deploy_ds13.py');shutil.copy2(OLD_PROTOCOL,EVID/'before_k100_protocol.py')
    IMAGE.parent.mkdir(parents=True);shutil.copy2(PACKAGE/'firmware/J280_DS13_S1_LIVE.fs',IMAGE);assert sha(IMAGE)==FS_SHA
    sys.path[:0]=[str(PACKAGE/'host'),str(PACKAGE/'host/vendor')]
    import protocol as p
    from hardware_mutex import SharedMutex
    import serial
    from serial.tools.list_ports import comports
    old=module('before_k100_protocol',EVID/'before_k100_protocol.py')
    ports=[x for x in comports() if (x.vid,x.pid)==(0x1a86,0x7523)]
    assert len(ports)==1 and ports[0].device=='COM5','Ambiguous or missing target COM5'
    record.update(port=dict(name=ports[0].device,hwid=ports[0].hwid),programmer_sha256=CLI_SHA,firmware=str(IMAGE));save()
    with SharedMutex():
        port=serial.Serial(port=None,baudrate=2000000,timeout=.03,write_timeout=.3,exclusive=True)
        port.dtr=False;port.rts=False;port.port='COM5';port.open();record['hardware_actions']=1;save()
        with port:
            port.reset_input_buffer()
            capture(port,'BEFORE_TRANSITION',.5,old.FrameParser)
            rows,proof=capture(port,'BEFORE_STOPPED',2.5,old.FrameParser)
            stopped(rows,proof,0x4b313030,0x4b313001,16);record['before_uart']=proof;save()
            cables=cli('JTAG_SCAN',['--scan-cables']);locations=re.findall(r'USB location:\s*(\d+)',cables)
            assert len(locations)==1 and 'GWU2X' in cables,'Ambiguous cable'
            base=['--device','GW2A-55C','--cable-index','0','--location',locations[0]]
            record['before_jtag']=jtag(cli('JTAG_BEFORE',base+['--operation_index','0']));save()
            assert record['before_jtag']==dict(id=0x281b,user=0x4b313030)
            assert sha(IMAGE)==FS_SHA
            with (EVID/'SRAM_ONCE.lock').open('x') as f:f.write('One authorized SRAM download. No automatic retry.\n')
            record.update(status='SRAM_WRITE_INTENT',sram_attempts=1);save()
            print('PRECHECK PASSED: K100 stopped and hardware released; programming qualified DS13 SRAM',flush=True)
            log=cli('SRAM_PROGRAM',base+['--operation_index','2','--fsFile',str(IMAGE)],120)
            assert re.search(r'SRAM\s+Program.*?Finished',log,re.I|re.S),'SRAM success not confirmed'
            record['after_jtag']=jtag(cli('JTAG_AFTER',base+['--operation_index','0']));save()
            assert record['after_jtag']==dict(id=0x281b,user=p.FIRMWARE_ID)
            record['status']='SRAM_PROGRAMMED_JTAG_VERIFIED';save()
            port.reset_input_buffer()
            capture(port,'AFTER_TRANSITION',3,p.FrameParser)
            rows,proof=capture(port,'AFTER_STOPPED',2.5,p.FrameParser)
            ts=stopped(rows,proof,p.FIRMWARE_ID,p.SCHEMA)
            assert all(tuple(r['manual_bits'])==p.S1_BITS and tuple(r['active_bits'])==p.S1_BITS for r in ts)
            parser=p.FrameParser();session=(time.time_ns()&0xffffffff) or 1;number=1000
            def request(opcode,group=0,payload=b''):
                nonlocal number
                assert opcode in p.READS or (opcode==p.WRITE_CONFIG and group==254 and payload==struct.pack('<BI',5,oldkit)),'Only reads and preservation of the previous encoder kit are permitted'
                number+=1;frame=p.request(opcode,number,group,payload,session=session,event=number)
                item=dict(opcode=opcode,request_id=number,raw_hex=frame.hex(),status='INTENT');record['uart_requests'].append(item);save()
                n=port.write(frame);item.update(status='WRITTEN',bytes=n);save();assert n==len(frame)
                raw=bytearray();end=time.monotonic()+3;ack=None
                while time.monotonic()<end and ack is None:
                    data=port.read(min(32768,max(1,port.in_waiting)));raw.extend(data)
                    for _,r in parser.feed(data):
                        assert r['identity_ok'],'Unexpected board'
                        if r['kind']=='ack' and r['request_id']==number:
                            assert r['opcode']==opcode;ack=r
                (EVID/f'REQUEST_{number}_RX.bin').write_bytes(raw)
                assert parser.errors==0 and ack is not None,'No clean acknowledgement; no automatic replay'
                item.update(status='ACK',ack=ack);save();assert ack['status']==0
                return ack
            oldkit=record['before_uart']['latest']['diagnostic']['kit'];assert oldkit in (0,1)
            request(p.HELLO);request(p.STATUS)
            gains=request(p.READ_GAINS,255);assert tuple(gains['stored_bits'])==p.S1_BITS
            configs={str(field):request(p.READ_CONFIG,254,bytes([field]))['config_read_value'] for field in (5,6,7,8)}
            if configs['5']!=oldkit:
                request(p.WRITE_CONFIG,254,struct.pack('<BI',5,oldkit));record['kit_selection_restored']=True;save()
                configs={str(field):request(p.READ_CONFIG,254,bytes([field]))['config_read_value'] for field in (5,6,7,8)}
            assert configs['5']==oldkit and configs['6']==(1040 if oldkit else 1080)
            assert configs['7']==1040 and configs['6'] in (1040,1080)
            rows,proof=capture(port,'FINAL_STOPPED_S1',2.5,p.FrameParser)
            ts=stopped(rows,proof,p.FIRMWARE_ID,p.SCHEMA)
            assert all(tuple(r['manual_bits'])==p.S1_BITS and tuple(r['active_bits'])==p.S1_BITS for r in ts)
            record.update(status='COMPLETE_STOPPED',final_uart=proof,S1_readback=gains,S1_bits=[f'{v:08x}' for v in p.S1_BITS],
                          configs=configs,manual_and_active_S1_exact=True,port_closed_after_verification=True,
                          running_control_periods='Not measured on board: no ZERO/RUN. Verified in fresh compiled RTL tests.',
                          uart_reads_and_previous_kit_only=True);save()
            print('DONE: DS13 SRAM/JTAG/UART identity and exact S1 verified; STOPPED; no ZERO/RUN or gain writes',flush=True)

if __name__=='__main__':
    assert sys.argv[1:]==['--execute-authorized']
    try:execute()
    except BaseException as exc:
        record.update(status='FAILED_OR_UNKNOWN_REVIEW_BEFORE_RETRY',error=repr(exc));raise
    finally:
        if EVID.exists():
            record['finished_local']=datetime.datetime.now().astimezone().isoformat();save()
            print('EVIDENCE='+str(EVID),flush=True)
