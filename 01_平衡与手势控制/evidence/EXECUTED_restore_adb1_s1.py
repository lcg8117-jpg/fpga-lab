"""Explicitly authorized one-time SRAM restore + S1 write/readback, never ZERO/RUN."""
from pathlib import Path
import datetime, hashlib, importlib.util, json, re, shutil, struct, subprocess, sys, time

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'outputs/ADB1旧版完整备份_20261103/旧版完整原件'
OUT=ROOT/'outputs/ADB1旧版_S1现场封存_20261103_0342'
EVID=OUT/'evidence'
G3=Path('D:/Codex_Scratch/SWD4_AutoTune_20261020/operations/GOWIN_Minimal_Integration_20261103/Deployment_G3_20261103/host')
CLI=Path('D:/Gowin/Gowin_V1.9.12.03_x64/Programmer/bin/programmer_cli.exe')
FS_SHA='64a16095e2077ed6f0da54f7f8694b7ae078578ed88db02bef020011fb45e8c6'
CLI_SHA='6cadcfe296ad8d58a2b9f23e855d39c3d18d59b61bbd4de68594139727ca5b93'
S1=(53.78,0.,210.14,17.67,0.,616.61)
BITS=['42571eb8','00000000','435223d7','418d5c29','00000000','441a270a']
IMAGE=Path('D:/Codex_Scratch/ADB1_S1_RESTORE_20261103_0342/adb1.fs')
record={'status':'PREPARED','sram_attempts':0,'nonvolatile_flash_written':False,
        'ZERO_sent':False,'RUN_sent':False,'motion_sent':False,'uart_transmitted_bytes':0,
        'authorization':'User requested restoring the old ADB1 firmware and S1 PID; user confirmed G3 stopped and closed.',
        'manual_function_test':'Awaiting user test; not claimed passed','calls':[],'uart_requests':[]}

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write_json(p,v):
    t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');t.replace(p)
def save():write_json(EVID/'DEPLOYMENT_RESULT.json',record)
def load_module(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def bits(values):return [f'{v:08x}' for v in struct.unpack('<6I',struct.pack('<6f',*values))]

def prepare():
    assert not OUT.exists(), 'Preserve previous results'
    assert sha(OLD/'firmware/J280_ADB1_FastP_1_5ms_50MHz.fs')==FS_SHA
    assert sha(CLI)==CLI_SHA and bits(S1)==BITS
    EVID.mkdir(parents=True)
    shutil.copytree(OLD,OUT/'原版完整包')
    result=subprocess.run([sys.executable,'-B',str(OUT/'原版完整包/verify_delivery.py')],capture_output=True,timeout=120)
    (EVID/'ORIGINAL_DELIVERY_CHECK.log').write_bytes(result.stdout+result.stderr)
    assert result.returncode==0, (result.stdout+result.stderr).decode(errors='replace')
    IMAGE.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(OLD/'firmware/J280_ADB1_FastP_1_5ms_50MHz.fs',IMAGE)
    assert sha(IMAGE)==FS_SHA
    for name in ('hardware_mutex.py','protocol.py'):
        shutil.copy2(G3/name,EVID/('g3_'+name))
    write_json(OUT/'S1参数.json',{'gains_order':['angleP','angleI','angleD','positionP','positionI','positionD'],
        'gains':S1,'IEEE754':BITS,'period_ms':{'angleP':1,'angleD':5,'positionP':5,'positionD':25},
        'original_fs_defaults':[49,0,260,25,0,620],'applied_in_board_RAM':True,'adaptive_enabled':False,
        'note':'Old FS is unchanged. SRAM image and runtime S1 are volatile. No new S1-default firmware was compiled.'})
    record.update(firmware_sha256=FS_SHA,firmware=str(IMAGE),programmer_sha256=CLI_SHA,
                  original_delivery_recheck='PASS',requested_gains=S1,requested_bits=BITS)
    save()
    print('PREPARED: qualified original image, full package and S1 metadata',flush=True)

def capture(port,label,seconds,parser_type):
    parser=parser_type();raw=bytearray();rows=[];end=time.monotonic()+seconds
    while time.monotonic()<end:
        block=port.read(min(32768,max(1,port.in_waiting)));raw.extend(block)
        assert len(raw)<4*1024*1024
        rows.extend(item for _,item in parser.feed(block))
    (EVID/(label+'.bin')).write_bytes(raw)
    summary={'frames':len(rows),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
             'crc_errors':parser.crc_errors,'format_errors':parser.format_errors,
             'latest':{kind:next((r for r in reversed(rows) if r['kind']==kind),None)
                       for kind in ('telemetry','extension','diagnostic','ack')}}
    write_json(EVID/(label+'.json'),summary)
    return rows,summary

def stopped(rows,summary,fw,schema,version,variant):
    ts=[r for r in rows if r['kind']=='telemetry']
    assert len(ts)>=10, 'Insufficient fresh status telemetry'
    assert summary['crc_errors']==0 and summary['format_errors']==0, 'Fresh RX not clean'
    assert ts[-1]['sequence']>ts[0]['sequence'] and ts[-1]['board_ms']>ts[0]['board_ms']
    assert all((r['firmware_id'],r['schema'],r['wire_version'],r['variant'])==(fw,schema,version,variant) for r in ts)
    assert all(r['mode'] in (0,1,4) and not r['motor_permitted_at_completion'] and r['actual_applied_q15']==0 for r in ts)

def cli(label,args,timeout=30):
    row={'label':label,'args':args,'status':'INTENT'};record['calls'].append(row);save()
    r=subprocess.run([str(CLI),*args],capture_output=True,timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW)
    raw=r.stdout+r.stderr;(EVID/(label+'.log')).write_bytes(raw)
    row.update(exit_code=r.returncode,status='RETURNED',log_sha256=sha(EVID/(label+'.log')));save()
    assert r.returncode==0,(label,r.returncode)
    return raw.decode('utf-8',errors='replace')
def jtag(text):
    return {key:int(re.search(pattern,text,re.I).group(1),16) for key,pattern in
            [('id',r'ID Code is:\s*0x([0-9a-f]+)'),('user',r'User Code is:\s*0x([0-9a-f]+)')]}

def execute():
    assert not (EVID/'SRAM_ONCE.lock').exists()
    sys.path.insert(0,str(OUT/'原版完整包'))
    from host import protocol as p
    old=load_module('g3_protocol_reference',EVID/'g3_protocol.py')
    mutex=load_module('hardware_lease',EVID/'g3_hardware_mutex.py')
    import serial
    from serial.tools.list_ports import comports
    ports=[x for x in comports() if x.vid==0x1a86 and x.pid==0x7523]
    assert len(ports)==1 and ports[0].device=='COM5'
    record['port']={'name':ports[0].device,'hwid':ports[0].hwid};save()
    with mutex.SharedMutex():
        port=serial.Serial(port=None,baudrate=2000000,timeout=.03,write_timeout=.3,exclusive=True)
        port.dtr=False;port.rts=False;port.port='COM5';port.open()
        with port:
            capture(port,'BEFORE_TRANSITION',.5,old.FrameParser)
            rows,proof=capture(port,'BEFORE_STOPPED',2.5,old.FrameParser)
            stopped(rows,proof,0x56354733,0x46443405,5,5)
            record['before_uart']=proof;save()
            cables=cli('JTAG_SCAN',['--scan-cables'])
            loc=re.findall(r'USB location:\s*(\d+)',cables)
            assert len(loc)==1 and 'GWU2X' in cables
            base=['--device','GW2A-55C','--cable-index','0','--location',loc[0]]
            record['before_jtag']=jtag(cli('JTAG_BEFORE',base+['--operation_index','0']));save()
            assert record['before_jtag']=={'id':0x281b,'user':0x56354733}
            assert sha(IMAGE)==FS_SHA
            with (EVID/'SRAM_ONCE.lock').open('x') as f:f.write('One authorized original ADB1 SRAM restore. No automatic retry.\n')
            record.update(status='SRAM_WRITE_INTENT',sram_attempts=1);save()
            print('PRECHECK PASSED; programming original ADB1 SRAM',flush=True)
            log=cli('SRAM_PROGRAM',base+['--operation_index','2','--fsFile',str(IMAGE)],120)
            assert re.search(r'SRAM\s+Program.*?Finished',log,re.I|re.S)
            record['after_jtag']=jtag(cli('JTAG_AFTER',base+['--operation_index','0']));save()
            assert record['after_jtag']=={'id':0x281b,'user':0x41444231}
            record['status']='SRAM_PROGRAMMED_JTAG_VERIFIED';save()
            capture(port,'AFTER_TRANSITION',3,p.FrameParser)
            rows,proof=capture(port,'AFTER_STOPPED',2.5,p.FrameParser)
            stopped(rows,proof,p.FIRMWARE,p.SCHEMA,p.VERSION,p.VARIANT)
            record['after_uart']=proof;save()
            parser=p.FrameParser();request_id=1000
            def request(opcode,group=0,payload=b''):
                nonlocal request_id
                assert opcode in (p.STATUS,p.READ_CONFIG,p.READ_GAINS,p.SET_GAINS,p.ADAPT_DISABLE)
                request_id+=1;frame=p.request(opcode,request_id,group,payload)
                item={'request_id':request_id,'opcode':opcode,'group':group,'raw_hex':frame.hex(),'status':'INTENT'}
                record['uart_requests'].append(item);save()
                count=port.write(frame);record['uart_transmitted_bytes']+=count
                item.update(status='WRITTEN',bytes=count);save();assert count==len(frame)
                end=time.monotonic()+3;raw=bytearray();ack=None
                while time.monotonic()<end and ack is None:
                    data=port.read(min(32768,max(1,port.in_waiting)));raw.extend(data)
                    for _,r in parser.feed(data):
                        assert p.identity_matches(r),'Unexpected board identity'
                        if r['kind']=='ack' and r['request_id']==request_id:
                            assert r['opcode']==opcode;ack=r
                (EVID/f'REQUEST_{request_id}_RX.bin').write_bytes(raw)
                if ack is None:
                    item['status']='ACK_UNKNOWN_NO_RETRY';save();raise RuntimeError('ACK absent; do not replay mutation')
                item.update(status='ACK',ack=ack);save();assert ack['status']==0,ack
                return ack
            request(p.STATUS)
            caps=request(p.READ_CONFIG,254,b'\x08')
            assert caps['config_group']==254 and caps['config_id']==8 and caps['config_read_value']==15
            record['before_gains']=request(p.READ_GAINS,255)
            request(p.ADAPT_DISABLE)
            written=request(p.SET_GAINS,255,p.gains_payload(S1))
            assert bits(written['stored_gains'])==BITS
            readback=request(p.READ_GAINS,255)
            assert bits(readback['stored_gains'])==BITS and readback['stored_revision']==written['stored_revision']
            record.update(S1_readback=readback,S1_exact_bits=bits(readback['stored_gains']),status='SRAM_AND_S1_READBACK_VERIFIED');save()
            rows,proof=capture(port,'FINAL_STOPPED_S1',3,p.FrameParser)
            stopped(rows,proof,p.FIRMWARE,p.SCHEMA,p.VERSION,p.VARIANT)
            ts=[r for r in rows if r['kind']=='telemetry']
            assert all(bits(r['manual_gains'])==BITS and not r['adaptive_enabled_at_launch'] for r in ts)
            ex=proof['latest']['extension']
            if ex:assert ex['adaptation_enabled']==0 and ex['adaptation_active']==0
            record.update(final_uart=proof,manual_S1_verified=True,old_adaptive_disabled=True,
                          active_S1_verified=all(bits(r['active_gains'])==BITS for r in ts),
                          runtime_control_period_verification='Pending user ZERO/RUN; no control transaction was started by this script',
                          status='COMPLETE_STOPPED_READY_FOR_USER_TEST');save()
            print('DONE: original ADB1 + exact S1 readback; adaptive disabled; no ZERO/RUN',flush=True)

if __name__=='__main__':
    assert sys.argv[1:]==['--execute-authorized']
    prepare()
    try:execute()
    except BaseException as exc:
        record.update(status='FAILED_OR_UNKNOWN_REVIEW_RECORD_BEFORE_ANY_RETRY',error=repr(exc));raise
    finally:
        record['finished_local']=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat();save()
