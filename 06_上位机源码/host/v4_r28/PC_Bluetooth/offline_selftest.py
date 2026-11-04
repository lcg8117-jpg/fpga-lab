"""Offline codec/session/control/log QA; no window, thread, scan, or device."""
from pathlib import Path
import ast
import collections
import importlib.abc
import json
import struct
import sys
import tempfile
import threading
import time
import zipfile

ROOT=Path(__file__).resolve().parent
FORBIDDEN={'serial','bleak','winrt','bluetooth','pybluez'}
attempts=[]
class BlockDeviceImports(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in FORBIDDEN:
            attempts.append(fullname);raise RuntimeError('Offline QA forbids '+fullname)
sys.meta_path.insert(0,BlockDeviceImports())
threads_before=tuple(threading.enumerate())
import protocol as p
import client
import app
import session_log

def ack(rid=1,opcode=p.STATUS,status=0,mode=1,flags=0):
    b=bytearray(80);b[:4]=b'FD2A';b[4:8]=bytes((4,opcode,status,4))
    struct.pack_into('<I',b,8,rid);struct.pack_into('<6f',b,24,*p.DEFAULT_GAINS)
    b[58]=mode;struct.pack_into('<I',b,60,flags);struct.pack_into('<I',b,72,p.FW)
    return p.seal(b)

def diagnostic(session=123):
    b=bytearray(256);b[:4]=b'FD2D';b[4:8]=bytes((4,4,p.LAYOUT,1))
    struct.pack_into('<II',b,16,p.SCHEMA,p.FW);struct.pack_into('<II',b,24,500,500)
    b[33]=1;struct.pack_into('<iII',b,40,-99,0,1040);struct.pack_into('<I',b,52,1040)
    struct.pack_into('<HHHH',b,72,12000,750,12010,751)
    struct.pack_into('<II',b,80,500,500);struct.pack_into('<qqqq',b,88,120,100,98,99)
    b[123]=3;struct.pack_into('<6f',b,128,*p.DEFAULT_GAINS);struct.pack_into('<I',b,164,session)
    b[189]=1;struct.pack_into('<5H',b,190,1000,5000,5000,25000,5000)
    struct.pack_into('<6I',b,200,510,495,475,500,500,0)
    return p.seal(b)

def compact(seq=1,mode=3,actual=99,control=98,final=120):
    b=bytearray(48);b[:4]=b'FD2B';b[4:8]=bytes((4,4,133,mode))
    struct.pack_into('<IIIqii',b,8,seq,500+seq*5,500+seq*5,actual,control-actual,final-actual)
    struct.pack_into('<hhhh',b,36,2,0,100,4)
    return p.seal(b)

def expect_failure(fn):
    try:fn()
    except (ValueError,RuntimeError):return True
    return False

def run():
    checks=[]
    def check(name,value):
        if not value:raise AssertionError(name)
        checks.append(name)
    packets=[ack(),diagnostic(),compact()]
    parser=p.FrameParser();stream=b''.join(packets);out=[]
    for i in range(0,len(stream),7):out.extend(parser.feed(stream[i:i+7]))
    check('fragmented A80 D256 B48 parsed exactly',[raw for raw,_ in out]==packets)
    check('valid capture has no CRC format discard errors',not(parser.crc_errors or parser.format_errors or parser.discarded_bytes or parser.buffer))
    d=p.decode(diagnostic())
    check('R28 default bank uses requested current parameters',p.DEFAULT_GAINS==(53.78,0.0,210.14,17.67,0.0,616.61))
    check('layout5 reports independent P D and upload periods',
        d['control_profile']==1 and tuple(d[k] for k in ('angle_p_period_us','position_p_period_us','angle_d_period_us','position_d_period_us','upload_period_us'))==(1000,5000,5000,25000,5000))
    check('layout5 preserves completed D and P epoch provenance',
        (d['adc_batch_sequence'],d['angle_d_epoch'],d['position_d_epoch'],d['position_p_epoch'],d['completed_sample_epoch'],d['d_gain_revision'])==(510,495,475,500,500,0))
    check('compact uptime sample epoch strides5 while packet sequence strides1',
        p.decode(compact(2))['control_epoch']-p.decode(compact())['control_epoch']==5 and p.decode(compact(2))['sequence']-p.decode(compact())['sequence']==1)
    for label,base,offset,fmt,value in (
        ('ACK old firmware',ack(),72,'I',0x56345237),('D old firmware',diagnostic(),20,'I',0x56345237),
        ('D old schema',diagnostic(),16,'I',0x56345207),('D old layout',diagnostic(),6,'B',4),
        ('D wrong variant',diagnostic(),5,'B',5),('D wrong wire',diagnostic(),4,'B',5),
        ('B wrong variant',compact(),5,'B',5),('D mixed epoch',diagnostic(),80,'I',501),
        ('D mixed completed sample epoch',diagnostic(),216,'I',501),('D mixed completed gain revision',diagnostic(),220,'I',1)):
        b=bytearray(base);struct.pack_into('<'+fmt,b,offset,value)
        check('reject '+label,expect_failure(lambda:p.decode(p.seal(b))))
    bad=bytearray(compact());bad[32]^=1
    parser=p.FrameParser();got=parser.feed(bytes(bad)+diagnostic())
    check('CRC corruption rejected and parser resynchronizes',len(got)==1 and got[0][0]==diagnostic() and parser.crc_errors>=1)
    check('R28 codec rejects full T',expect_failure(lambda:p.decode(p.seal(b'FD2T'+bytes(252)))))
    # Portable byte-for-byte vectors generated from qualified R22 code.
    golden=json.loads((ROOT/'REQUEST_GOLDEN.json').read_text('utf-8'))
    for vector in golden['vectors']:
        payload=bytes.fromhex(vector['payload_hex'])
        actual=p.request(vector['opcode'],golden['request_id'],vector['group'],payload,version=4,
            session=golden['session'],event=golden['event'],route=golden['route'])
        check('qualified R22 request bytes '+str(vector['opcode'])+' '+payload.hex(),actual.hex()==vector['request_hex'])
    for index in (1,4):
        values=list(p.DEFAULT_GAINS);values[index]=1
        check('nonzero Ki rejected '+str(index),expect_failure(lambda:p.gains_payload(values)))
        check('raw nonzero Ki rejected '+str(index),expect_failure(lambda:p.request(p.SET_GAINS,1,255,struct.pack('<6f',*values))))
    for op in (0x22,0x30,0x31,0x32,0x33,0x40,0x46):
        check('unused opcode absent '+str(op),expect_failure(lambda:p.request(op,1)))
    owner=client.BLEOwner('OFFLINE_NO_CONNECTION',ROOT/'logs');owner.session=123
    owner.state.connected=True
    owner._receive(compact(),p.decode(compact()))
    check('unbound compact never authenticates seeds display or graph',not owner.state.authenticated and owner.state.latest is None and not owner.state.history)
    owner._receive(diagnostic(999),p.decode(diagnostic(999)))
    check('D from another logical session ignored',owner.state.diagnostic is None and not owner.identity_d)
    owner._receive(diagnostic(),p.decode(diagnostic()))
    check('D alone cannot unlock controls',owner.identity_d and not owner.state.authenticated)
    owner._receive(ack(),p.decode(ack()))
    check('unmatched ACK cannot unlock controls',not owner.identity_ack and not owner.state.authenticated)
    owner.pending[1]=(p.STATUS,0,b'',time.monotonic())
    owner._receive(ack(),p.decode(ack()))
    check('matching STATUS ACK plus same-session D authenticates',owner.state.authenticated)
    owner._receive(compact(),p.decode(compact()))
    check('bound compact has actual current and final targets',owner.state.latest['wide_encoder']==99 and owner.state.latest['wide_target']==98 and owner.state.latest['final_target']==120)
    check('bound coherent compact seeds history',len(owner.state.history)==1 and owner.state.snapshot()['control_fresh'])
    owner._receive(compact(2),p.decode(compact(2)))
    check('sample epoch stride5 creates no packet loss',owner.state.sequence_gaps==0 and len(owner.state.history)==2)
    text=app.telemetry_text(owner.state.snapshot())
    check('tracking and final errors displayed separately','跟踪误差 +1计数' in text[2] and '最终误差 -21计数' in text[2])
    units=owner.state.snapshot();units['latest']=dict(units['latest'],angle_error=4096,target_angle_error=-4096,wide_encoder=1040)
    unit_text=app.telemetry_text(units)
    check('Forest angle4096 counts is345deg and arm1040 counts remains360deg',
        '摆杆角误差 +345.000°（目标 -345.000°）' in unit_text[2] and '实测 1040 / 360.000°' in unit_text[1])
    owner.command(p.POSITION,payload=b'\x06')
    owner.command(p.STOP)
    check('STOP cancels queued position actions',all(x[2] in p.SAFE for x in owner.queue.queue))
    check('running gains denied',expect_failure(lambda:owner.set_gains(p.DEFAULT_GAINS)))
    check('running ZERO denied',expect_failure(lambda:owner.command(p.ZERO)))
    owner.state.latest_rx=time.monotonic()-.6
    check('stale compact cannot authorize motion',expect_failure(lambda:owner.command(p.POSITION,payload=b'\x01')))
    class FakePort:
        def __init__(self):self.writes=[]
        def write(self,raw):self.writes.append(raw);return len(raw)
    owner.port=FakePort()
    check('final send rechecks motion freshness',expect_failure(lambda:owner._send(p.POSITION,payload=b'\x01')) and not owner.port.writes)
    owner.state.latest=None;owner.state.latest_rx=0
    owner.state.last_ack=p.decode(ack());owner.state.last_ack_rx=time.monotonic()
    check('fresh stopped state allows parameter write',owner.parameter_write_status()[0])
    for flags in (2,4,8):
        owner.state.last_ack=p.decode(ack(flags=flags))
        check('capture run motor ACK state blocks parameters '+str(flags),not owner.parameter_write_status()[0])
    owner.state.last_ack=p.decode(ack())
    owner.state.diagnostic['zero_valid']=0
    check('RUN requires valid zero',expect_failure(lambda:owner.command(p.RUN)))
    owner.state.diagnostic['zero_valid']=1
    owner.command(p.RUN)
    check('pending RUN blocks parameter submit',not owner.parameter_write_status()[0])
    owner.command(p.STOP)
    check('STOP is allowed without authentication',bool(owner.queue.qsize()))
    owner.state.authenticated=False
    owner.command(p.STOP)
    # Exercise the actual ZIP writer synchronously, without starting its thread.
    with tempfile.TemporaryDirectory(prefix='r27_host_qa_') as directory:
        directory=Path(directory)
        logger=session_log.SessionLogger(directory,4,min_free_bytes=0,reserve_bytes=1024)
        logger.partial_path=directory/'qa.partial.zip';logger.archive_path=directory/'qa.zip'
        for raw in packets:check('logger accepts '+raw[:4].decode(),logger.submit(raw,p.decode(raw)))
        logger.queue.put(None);logger._run()
        check('bounded raw logger completes lossless archive',not logger.error and [x[2] for x in session_log.iter_records(logger.archive_path)]==packets)
        with zipfile.ZipFile(logger.archive_path) as archive:metadata=json.loads(archive.read('session.json'))
        check('log metadata matches real R28 identity and layout',metadata['firmware']=='0x56345239' and metadata['schema']=='0x56345208' and metadata['diagnostic_layout']==5)
        check('log metadata identifies compact and separate errors','compact48' in metadata['components'] and 'final error=actual-final goal' in metadata['position_provenance'] and '345deg/4096' in metadata['angle_provenance'])
        check('log metadata separates 1kHz controller from200Hz archive',
            metadata['control_periods_us']==dict(angle_p=1000,position_p=5000,angle_d=5000,position_d=25000,upload=5000) and metadata['adc_window_samples']==64 and '200Hz' in metadata['control_epoch_provenance'] and 'completed D' in metadata['d_provenance'])
    tree=ast.parse((ROOT/'app.py').read_text('utf-8'))
    init=next(x for x in ast.walk(tree) if isinstance(x,ast.FunctionDef) and x.name=='__init__')
    check('UI constructor cannot auto-connect/start',not any(isinstance(x,ast.Call) and isinstance(x.func,ast.Attribute) and x.func.attr in ('connect','start') for x in ast.walk(init)))
    check('no forbidden device imports',not attempts and not any(x.split('.')[0] in FORBIDDEN for x in sys.modules))
    check('no threads started',tuple(threading.enumerate())==threads_before)
    report=dict(status='PASS',scope='Synthetic offline Host codec/session/STOP/stale/metadata checks; no control-performance qualification',
        checks=len(checks),passed=checks,device_import_attempts=attempts,threads_started=0,windows_started=0,connections=0,commands_sent_to_hardware=0)
    (ROOT/'OFFLINE_CHECKS.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='passed'},ensure_ascii=False))

if __name__=='__main__':run()
