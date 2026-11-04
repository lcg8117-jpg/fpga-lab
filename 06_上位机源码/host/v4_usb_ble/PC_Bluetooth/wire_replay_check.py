"""Replay real simulated BLE wire bytes through the shipping Host offline."""
from pathlib import Path
import argparse
import collections
import hashlib
import importlib.abc
import json
import struct
import sys
import threading
import time

class DeviceBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'serial','bleak','winrt','bluetooth'}:raise RuntimeError('Device imports forbidden')
sys.meta_path.insert(0,DeviceBlocker())
before=tuple(threading.enumerate())
import protocol as p
from client import BLEOwner

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--capture',type=Path,required=True);ap.add_argument('--report',type=Path,required=True)
    args=ap.parse_args();raw=args.capture.read_bytes();parser=p.FrameParser();items=[]
    expected_gains=(53.78,0.0,210.14,17.67,0.0,616.61)
    expected_periods=(1000,5000,5000,25000,5000)
    assert (p.FW,p.SCHEMA,p.LAYOUT,p.DEFAULT_GAINS)==(0x56345239,0x56345208,5,expected_gains),'Shipping codec must identify R28'
    # The board stores IEEE754 binary32 gains; retain exact wire equality.
    expected_gains=struct.unpack('<6f',p.gains_payload(expected_gains))
    for i in range(0,len(raw),19):items.extend(parser.feed(raw[i:i+19]))
    counts=dict(collections.Counter(x['kind'] for _,x in items))
    assert counts.get('diagnostic',0)>0 and counts.get('ack',0)>0 and counts.get('compact',0)>1,counts
    assert not(parser.crc_errors or parser.format_errors or parser.discarded_bytes or parser.buffer)
    assert sum(len(packet) for packet,_ in items)==len(raw),'Every captured byte must be decoded exactly once'
    diagnostics=[]
    for packet,item in items:
        if item['kind']!='diagnostic':continue
        assert (item['firmware_id'],item['schema'],item['layout'])==(p.FW,p.SCHEMA,p.LAYOUT)
        diagnostics.append(dict(session=item['session'],profile=packet[189],periods_us=struct.unpack_from('<5H',packet,190),
            adc_batch_sequence=struct.unpack_from('<I',packet,200)[0],angle_d_epoch=struct.unpack_from('<I',packet,204)[0],
            position_d_epoch=struct.unpack_from('<I',packet,208)[0],position_p_epoch=struct.unpack_from('<I',packet,212)[0],
            completed_sample_epoch=struct.unpack_from('<I',packet,216)[0],d_gain_revision=struct.unpack_from('<I',packet,220)[0],
            active_revision=item['active_revision'],active_gains=item['active_gains'],last_completed_epoch=item['last_completed_epoch']))
    fast_diagnostics=[d for d in diagnostics if d['profile']==1 and d['session']!=0]
    assert fast_diagnostics,'Capture must contain a warmed, session-bound fast-P diagnostic'
    for diagnostic in fast_diagnostics:
        assert diagnostic['periods_us']==expected_periods,diagnostic
        assert diagnostic['completed_sample_epoch']==diagnostic['last_completed_epoch'],diagnostic
        assert diagnostic['d_gain_revision']==diagnostic['active_revision'],diagnostic
        assert all((diagnostic['completed_sample_epoch']-diagnostic[field])&0xffffffff<0x80000000
                   for field in ('angle_d_epoch','position_d_epoch','position_p_epoch')),diagnostic
    final_fast=fast_diagnostics[-1];session=final_fast['session']
    assert final_fast['adc_batch_sequence']>0 and final_fast['active_gains']==expected_gains,final_fast
    owner=BLEOwner('OFFLINE_NEVER_OPENED',args.report.parent/'unused_logs');owner.session=session;owner.state.connected=True
    # Reconstruct only an actual captured READ_GAINS read as pending. No
    # fabricated ACK/header/identity or transmitted request is introduced.
    read_ack=next(item for _,item in items if item['kind']=='ack' and item['opcode']==p.READ_GAINS and item['status']==0 and item['stored_gains']==expected_gains)
    owner.pending[read_ack['request_id']]=(p.READ_GAINS,255,b'',time.monotonic())
    early=bound=0
    for packet,item in items:
        if item['kind']=='compact':
            if owner.state.authenticated:bound+=1
            else:early+=1
        owner._receive(packet,item)
    assert owner.identity_ack and owner.identity_d and owner.state.authenticated
    assert not owner.identity_failed and not owner.state.error
    assert owner.state.diagnostic['session']==session
    assert owner.state.diagnostic['firmware_id']==p.FW and owner.state.diagnostic['schema']==p.SCHEMA
    assert owner.state.diagnostic['active_gains']==expected_gains
    assert owner.state.independent==expected_gains
    assert bound>0 and owner.state.latest is not None and owner.state.history
    assert owner.state.unbound_compact==early
    history=list(owner.state.history)
    epoch_steps=[(b['control_epoch']-a['control_epoch'])&0xffffffff for a,b in zip(history,history[1:])]
    running_steps=[(b['control_epoch']-a['control_epoch'])&0xffffffff for a,b in zip(history,history[1:])
                   if a['mode']==b['mode']==3 and a['control_epoch'] and b['control_epoch']]
    # STOP/startup may hold a completed epoch. Check the nominal publication
    # cadence only within running coherent data, allowing whole-frame gaps.
    if running_steps:
        assert all(0<step<0x80000000 and step%5==0 for step in running_steps),running_steps
        assert 5 in running_steps,'Running capture must show at least one nominal 5ms compact interval'
    assert tuple(threading.enumerate())==before
    report=dict(status='PASS',scope='Real RTL BLE-UART bytes decoded and session-bound by shipping Host; no physical plant/hardware acceptance',
        capture=str(args.capture),capture_sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),counts=counts,
        parser_crc_errors=parser.crc_errors,parser_format_errors=parser.format_errors,parser_discarded_bytes=parser.discarded_bytes,
        identity_session=session,firmware=f'{p.FW:08X}',schema=f'{p.SCHEMA:08X}',layout=p.LAYOUT,
        observed_zero_q4=owner.state.diagnostic['zero_q4'],active_gains=owner.state.diagnostic['active_gains'],
        fast_diagnostic_count=len(fast_diagnostics),final_fast_diagnostic=final_fast,
        compact_nominal_period_ms=5,compact_observed_epoch_steps=dict(collections.Counter(epoch_steps)),
        compact_running_epoch_steps=dict(collections.Counter(running_steps)),running_cadence_observed=bool(running_steps),
        actual_matched_read_request_id=read_ack['request_id'],compact_before_identity_ignored=early,compact_after_identity=bound,
        accepted_history=len(owner.state.history),threads_started=0,windows_started=0,device_connections=0,hardware_commands_sent=0)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
