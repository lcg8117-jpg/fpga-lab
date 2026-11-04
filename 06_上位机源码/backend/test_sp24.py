"""SP24 regressions. All traffic uses in-process fakes or loopback fixtures."""
import asyncio
import copy
from collections import deque
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

import main
from host_bridge import BridgeError, HostClient, TelemetryAdapter, identify, validate_action, validate_config

ROOT = Path(__file__).resolve().parents[1]


def snapshot():
    return dict(user_code='53503234', schema='53505424', connected=True, synced=True,
                status_age_s=.01, log_directory='explicit-offline-fixture',
                status=dict(mode=3, fault=0, revision=7, swing_active=False),
                telemetry={}, parameters={}, parameter_specs=[], requests={},
                precise_telemetry=dict(accepted=12, missing=0, duplicates=0, resets=0,
                    session_epoch=0, age_s=.01, stale=False, epoch_age_ms=2,
                    measurement_state='fresh', received_hz=100, board_hz=100),
                recording_error=None, recording_backlog=0, recording_lost_rows=0)


def sample(sequence=1, capture=100, stamp=None, encoder=2**63-20, epoch=0, **updates):
    return dict(sequence=sequence, capture_ms=capture, timestamp_ms=capture-2 if stamp is None else stamp,
                session_epoch=epoch, board_elapsed_ms=(sequence-1)*10, epoch_age_ms=2,
                host_monotonic=10+sequence*.01, received_at=1000+sequence*.01,
                alpha_q20=1048, alpha_dot_q20=-2097, theta_dot_q20=0,
                encoder_count=str(encoder), target_count_q32=str((encoder+3)*2**32),
                reference_rad_q20='524', pwm=-16384, pid_valid=True, velocity_valid=True,
                host_measurement_fresh=True, host_measurement_held=False,
                gap_before=False, sequence_gap=0, fault=0, mode=3, **updates)


def batch(rows=None, cursor=12, stream='fixture-stream', **updates):
    rows = [sample(i, 100+(i-1)*10, encoder=2**63-20+i) for i in range(1,13)] if rows is None else rows
    return dict(stream=stream,cursor=cursor,head=1,tail=cursor,overrun=False,samples=rows,**updates)


class Sp24IdentityTests(unittest.TestCase):
    def test_complete_identity_and_configuration(self):
        self.assertEqual(identify(snapshot()),'SP24')
        self.assertEqual(validate_config(dict(bridge_port=18371,firmware_variant='SP24'))['firmware_variant'],'SP24')
        with self.assertRaises(BridgeError): identify(dict(snapshot(),schema='53505423'))
        with self.assertRaises(BridgeError): identify(snapshot(),'SG21')

    def test_parameter_and_preset_guards(self):
        for name,value in [('move_degrees',360),('left_negative',1),('adaptive_mode',2)]:
            body=dict(action='set',request_id='explicit',name=name,value=value)
            self.assertEqual(validate_action(body,snapshot()),body)
        for name,value in [('move_degrees',0),('move_degrees',361),('left_negative',2),('adaptive_profile',1)]:
            with self.assertRaises(BridgeError): validate_action(dict(action='set',request_id='x',name=name,value=value),snapshot())
        value=json.loads((ROOT/'host/sp24/presets/field_baseline.json').read_text(encoding='utf-8-sig'))
        body=dict(action='apply-preset',request_id='preset-once',value=value)
        self.assertEqual(validate_action(body,snapshot()),body)
        with self.assertRaises(BridgeError): validate_action(body,dict(snapshot(),user_code='53413231',schema='53415421'))
        self.assertEqual(validate_action(dict(action='load-profile',request_id='shadow',value=dict(profile={},control=False)),snapshot())['value']['control'],False)


class Sp24AdapterTests(unittest.TestCase):
    def test_fresh_capture_cannot_refresh_an_expired_measurement_page(self):
        row=sample(); row.update(host_measurement_fresh=False,host_measurement_held=True)
        data,rows,_=self.observe(TelemetryAdapter(),waveform=batch([row],cursor=1),now=1000.01)
        self.assertIsNone(data['angle_deg'])
        self.assertEqual(data['angle_received_at'],0)
        self.assertEqual(data['arm_received_at'],row['received_at'])
        self.assertIsNotNone(data['duty_actual_percent'])
        self.assertIsNone(rows[0]['angle_deg'])

    def test_velocity_valid_gates_live_and_held_speed_without_hiding_angle_or_pwm(self):
        adapter=TelemetryAdapter()
        for index, valid in enumerate((False,True,False),1):
            row=sample(index,100+index*10)
            row['velocity_valid']=valid
            data,rows,_=self.observe(adapter,waveform=batch([row],cursor=index),now=1000+index*.01)
            self.assertIsNotNone(data['angle_deg']); self.assertIsNotNone(rows[0]['angle_deg'])
            self.assertIsNotNone(data['duty_actual_percent'])
            if valid:
                self.assertIsNotNone(data['angle_velocity_deg_s'])
                self.assertIsNotNone(data['arm_velocity_deg_s'])
                self.assertIsNotNone(rows[0]['angle_velocity_deg_s'])
            else:
                self.assertIsNone(data['angle_velocity_deg_s'])
                self.assertIsNone(data['arm_velocity_deg_s'])
                self.assertIsNone(rows[0]['angle_velocity_deg_s'])
            self.assertEqual(rows[0]['raw']['velocity_valid'],valid)
        held=sample(4,145,stamp=128); held.update(velocity_valid=False,host_measurement_held=True)
        data,rows,_=self.observe(adapter,waveform=batch([held],cursor=4),now=1000.04)
        self.assertIsNone(data['angle_velocity_deg_s']); self.assertIsNone(data['arm_velocity_deg_s'])
        self.assertIsNone(rows[0]['angle_velocity_deg_s']); self.assertIsNotNone(data['angle_deg'])

    def observe(self, adapter, source=None, waveform=None, now=1000.12):
        self.assertIn('waveform',inspect.signature(adapter.observe).parameters,'adapter requires the batch interface')
        return adapter.observe(snapshot() if source is None else source,now,waveform=batch() if waveform is None else waveform)

    def test_every_frame_retained_and_board_axis_is_not_batch_arrival_axis(self):
        data, rows, reset=self.observe(TelemetryAdapter())
        self.assertTrue(reset); self.assertEqual(len(rows),12)
        self.assertAlmostEqual(rows[-1]['plot_at']-rows[0]['plot_at'],.11)
        self.assertAlmostEqual(rows[-1]['plot_at']-rows[-1]['measurement_at'],.002)
        self.assertEqual(rows[0]['raw']['encoder_count'],str(2**63-19))
        self.assertEqual(data['precision']['origin_count'],str(2**63-19))
        self.assertEqual(rows[-1]['arm_relative_deg'],11/3)
        self.assertAlmostEqual(rows[-1]['target_relative_deg'],14/3)
        self.assertIsNone(data['arm_angle_deg'])
        self.assertTrue(data['telemetry_fresh'])

    def test_minimum_signed_wide_values_survive_without_float_subtraction(self):
        first=sample(1,encoder=-2**63)
        first['target_count_q32']=str(-2**95)
        first['reference_rad_q20']=str(-2**63)
        second=sample(2,capture=110,encoder=-2**63+1)
        data,rows,_=self.observe(TelemetryAdapter(),waveform=batch([first,second],cursor=2))
        self.assertEqual(rows[0]['raw']['target_count_q32'],str(-2**95))
        self.assertEqual(rows[0]['raw']['reference_rad_q20'],str(-2**63))
        self.assertEqual(rows[1]['arm_relative_deg'],1/3)
        json.dumps(dict(data=data,rows=rows),allow_nan=False)

    def test_sub_count_target_fraction_is_preserved_after_large_origin_subtraction(self):
        row=sample(); row['target_count_q32']=str((2**63-20)*2**32+1)
        _,rows,_=self.observe(TelemetryAdapter(),waveform=batch([row],cursor=1))
        self.assertEqual(rows[0]['target_relative_deg'],1/(3*2**32))

    def test_unsynced_status_does_not_discard_valid_capture_history(self):
        source=snapshot(); source['synced']=False
        data,rows,_=self.observe(TelemetryAdapter(),source)
        self.assertEqual(len(rows),12)
        self.assertFalse(data['status_fresh']); self.assertFalse(data['telemetry_fresh'])

    def test_rejected_batch_can_be_retried_without_losing_earlier_valid_rows(self):
        adapter=TelemetryAdapter(); good=sample(1)
        invalid=sample(2,110); invalid['capture_ms']='bad'
        with self.assertRaises(ValueError): self.observe(adapter,waveform=batch([good,invalid],cursor=2))
        _,rows,_=self.observe(adapter,waveform=batch([good,sample(2,110)],cursor=2))
        self.assertEqual([row['sequence'] for row in rows],[1,2])

    def test_duplicates_held_measurement_pid_invalid_and_250ms_stale(self):
        adapter=TelemetryAdapter(); source=snapshot()
        _,rows,_=self.observe(adapter,source)
        self.assertEqual(self.observe(adapter,source)[1],[])
        held=sample(13,220,stamp=208); held['host_measurement_held']=True; held['pid_valid']=False
        data,new,_=self.observe(adapter,source,batch([held],cursor=13),now=1000.13)
        self.assertIsNone(new[0]['angle_deg']); self.assertIsNone(new[0]['alpha_ref_deg'])
        self.assertEqual(new[0]['duty_actual_percent'],-50)
        source['precise_telemetry'].update(age_s=.251,stale=True)
        data,_,_=self.observe(adapter,source,batch([],cursor=13),now=1000.381)
        self.assertFalse(data['telemetry_fresh']); self.assertIsNone(data['angle_deg'])
        self.assertIsNone(data['duty_actual_percent'])

    def test_wrap_gap_reboot_and_stream_changes_keep_discontinuities(self):
        adapter=TelemetryAdapter()
        rows=[sample(0xffffffff,0xfffffffd,stamp=0xfffffffb),sample(0,7,stamp=5)]
        rows[0]['board_elapsed_ms']=0; rows[1]['board_elapsed_ms']=10
        _,converted,_=self.observe(adapter,waveform=batch(rows,cursor=2))
        self.assertAlmostEqual(converted[1]['plot_at']-converted[0]['plot_at'],.01)
        gap=sample(3,37,epoch=0); gap['gap_before']=True
        _,converted,_=self.observe(adapter,waveform=batch([gap],cursor=3))
        self.assertTrue(converted[0]['gap_before'])
        reboot=sample(0,3,stamp=1,epoch=1)
        reboot['board_elapsed_ms']=0
        _,converted,reset=self.observe(adapter,waveform=batch([reboot],cursor=4))
        self.assertTrue(converted[0]['gap_before']); self.assertTrue(reset)
        data,converted,reset=self.observe(adapter,waveform=batch([sample(1)],cursor=1,stream='new-stream'))
        self.assertTrue(reset); self.assertTrue(converted[0]['gap_before'])

    def test_gesture_pages_deduplicate_and_reconnect_does_not_claim_new_event(self):
        source=snapshot(); source['telemetry'].update(
            gesture_event=dict(event_count=3,event_ms=100,code=3,action=1,reason=7,new_since_poll=True),
            gesture_runtime=dict(poll_sequence=4,timestamp_ms=110,ready=True,blocked=True))
        adapter=TelemetryAdapter(); first,_,_=self.observe(adapter,source)
        repeated,_,_=self.observe(adapter,source,now=1001)
        self.assertFalse(first['gesture_event']['new_since_poll'])
        self.assertEqual(first['gesture_runtime']['received_at'],repeated['gesture_runtime']['received_at'])
        source['telemetry']['gesture_event'].update(event_count=4,event_ms=200)
        changed,_,_=self.observe(adapter,source,now=1001.1)
        self.assertTrue(changed['gesture_event']['new_since_poll'])
        adapter.invalidate()
        reconnected,_,_=self.observe(adapter,source,now=1001.2)
        self.assertFalse(reconnected['gesture_event']['new_since_poll'])
        self.assertEqual(first['gesture_runtime']['received_at'],reconnected['gesture_runtime']['received_at'])

    def test_first_gesture_after_reconnect_can_arrive_later_than_first_status(self):
        source=snapshot(); event=dict(event_count=3,event_ms=100,code=3,action=1,reason=7)
        source['telemetry']['gesture_event']=event
        adapter=TelemetryAdapter(); self.observe(adapter,source)
        adapter.invalidate(); source['telemetry']={}
        self.observe(adapter,source,now=1000.2)
        source['telemetry']['gesture_event']=dict(event,event_count=4,event_ms=200)
        data,_,_=self.observe(adapter,source,now=1000.3)
        self.assertFalse(data['gesture_event']['new_since_poll'])

    def test_device_epoch_first_retained_gesture_is_not_a_new_recognition(self):
        source=snapshot(); source['telemetry']['gesture_event']=dict(event_count=9,event_ms=500,code=3,action=1,reason=7)
        adapter=TelemetryAdapter(); self.observe(adapter,source)
        source['telemetry']['gesture_event']=dict(event_count=1,event_ms=10,code=1,action=2,reason=7)
        data,_,_=self.observe(adapter,source,batch([sample(1,10,stamp=8,epoch=1)],cursor=13),now=1000.3)
        self.assertFalse(data['gesture_event']['new_since_poll'])

    def test_same_event_can_update_acceptance_reason_without_becoming_new(self):
        source=snapshot(); source['telemetry']['gesture_event']=dict(event_count=3,event_ms=100,code=1,action=2,reason=12)
        adapter=TelemetryAdapter(); first,_,_=self.observe(adapter,source)
        source['telemetry']['gesture_event']['reason']=7
        data,_,_=self.observe(adapter,source,now=1000.3)
        self.assertEqual(data['gesture_event']['reason'],7)
        self.assertEqual(data['gesture_event']['received_at'],first['gesture_event']['received_at'])
        self.assertFalse(data['gesture_event']['new_since_poll'])


class Sp24DeliveryTests(unittest.TestCase):
    def test_vendor_runtime_is_original_and_manifest_covers_all_files(self):
        manifest=ROOT/'host/sp24/MANIFEST_SHA256.json'
        self.assertTrue(manifest.is_file(),'SP24 runtime and source manifest must be preserved')
        metadata=json.loads(manifest.read_text(encoding='utf-8'))
        self.assertEqual(metadata['source_zip_sha256'],'92c77bf2044a2e49fbe150ffa3d3c6b1ca5736b423384098adf20770a9d65abc')
        for path,digest in metadata['files'].items():
            self.assertEqual(hashlib.sha256((manifest.parent/path).read_bytes()).hexdigest(),digest,path)
        self.assertFalse(list(manifest.parent.rglob('*.fs')))

    def test_launcher_precise_help_does_not_open_hardware(self):
        result=subprocess.run([sys.executable,'-B','-X','utf8',str(ROOT/'launch_host.py'),'precise','--help'],
                              capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--offline-preview',result.stdout)

    def test_launcher_headless_explicit_offline_bridge_has_waveform_and_no_connection(self):
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1',0)); port=reservation.getsockname()[1]
        process=subprocess.Popen([sys.executable,'-B','-X','utf8',str(ROOT/'launch_host.py'),'precise',
                                  '--headless','--offline-preview','--bridge-port',str(port)],
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            client=HostClient(port,timeout=.3)
            deadline=time.monotonic()+8
            while True:
                try: state=client.status(); break
                except BridgeError:
                    if process.poll() is not None or time.monotonic()>=deadline:
                        process.terminate(); output,error=process.communicate(timeout=5)
                        self.fail(output+error)
                    time.sleep(.03)
            self.assertEqual(identify(state),'SP24')
            self.assertFalse(state['connected']); self.assertFalse(state['synced'])
            page=client.waveform(); self.assertEqual(page['samples'],[]); self.assertEqual(page['cursor'],0)
        finally:
            if process.poll() is None: process.terminate()
            process.communicate(timeout=5)


class Sp24LoopbackTests(unittest.TestCase):
    def setUp(self):
        bridge=ROOT/'host/sp24_bridge.py'
        self.assertTrue(bridge.is_file(),'read-only bridge is required')
        spec=importlib.util.spec_from_file_location('sp24_fixture_bridge',bridge)
        self.bridge=importlib.util.module_from_spec(spec); spec.loader.exec_module(self.bridge)
        host_path=ROOT/'host/sp24/gesture/host'
        sys.path.insert(0,str(host_path))
        self.addCleanup(lambda: sys.path.remove(str(host_path)))
        # The process uses only this delivered SP24 stack for the explicit fixture.
        import swing_tune_host
        self.vendor=swing_tune_host
        self.engine=self.vendor.Engine(tempfile.gettempdir(),allow_device=False)
        self.bridge.install(self.vendor)
        self.server=self.vendor.start_bridge(self.engine,0)
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.client=HostClient(self.server.server_port)

    def receive(self,count=12,limit=6000):
        self.engine.wave=self.vendor.TelemetryHistory(limit=limit)
        for i in range(count):
            raw=sample(i+1,100+i*10,encoder=-2**63+i)
            for name in ('encoder_count','target_count_q32','reference_rad_q20'): raw[name]=int(raw[name])
            raw.update(flags=0,fault=0,mode=3,motor=False,zero_valid=False,saturation=False,angle_revision=1,position_revision=1)
            self.engine.wave.receive(raw,at=time.monotonic())

    def test_cursor_pages_are_complete_lossless_and_origin_is_rejected(self):
        self.receive()
        first=self.client.exchange('/waveform?limit=5')
        second=self.client.exchange('/waveform?after=5&stream='+first['stream']+'&limit=5')
        third=self.client.waveform(after=10,stream=first['stream'])
        self.assertEqual([r['sequence'] for r in first['samples']+second['samples']+third['samples']],list(range(1,13)))
        self.assertEqual(first['samples'][0]['encoder_count'],str(-2**63))
        self.assertIsInstance(first['samples'][0]['received_at'],float)
        request=urllib.request.Request(self.client.base+'/waveform',headers={'X-J280-Local':'1','Origin':'http://localhost:5173'})
        with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(request)
        self.assertEqual(error.exception.code,403)
        self.assertEqual(self.engine.wave.accepted,12)

    def test_eviction_foreign_stream_and_bad_limit_are_explicit(self):
        self.receive(12,limit=5)
        page=self.client.waveform(after=1,stream='obsolete')
        self.assertTrue(page['overrun']); self.assertEqual(page['head'],8)
        self.assertEqual(page['cursor'],12); self.assertEqual(len(page['samples']),5)
        for query in ('limit=513','limit=0','after=-1','limit=NaN'):
            with self.assertRaises(BridgeError): self.client.exchange('/waveform?'+query)
        old_stream=page['stream']; self.receive(1)
        page=self.client.waveform(after=12,stream=old_stream)
        self.assertNotEqual(page['stream'],old_stream); self.assertTrue(page['overrun'])

    def test_status_wide_values_and_nonfinite_json_are_portable(self):
        self.receive(1)
        self.engine.wave.last['alpha_rad']=float('nan')
        value=self.client.status()
        self.assertEqual(value['precise_latest']['encoder_count'],str(-2**63))
        self.assertIsNone(value['precise_latest']['alpha_rad'])
        self.assertIsNone(self.client.waveform()['samples'][0]['alpha_rad'])

    def test_same_stream_eviction_is_reported_without_mutating_original_history(self):
        self.receive(1,limit=5)
        first=self.client.waveform()
        for i in range(2,9):
            raw=dict(self.engine.wave.last,sequence=i,capture_ms=100+i*10,timestamp_ms=98+i*10)
            self.engine.wave.receive(raw,at=time.monotonic())
        before=list(self.engine.wave.rows)
        page=self.client.waveform(after=1,stream=first['stream'])
        self.assertTrue(page['overrun']); self.assertEqual(page['head'],4)
        self.assertEqual(before,list(self.engine.wave.rows))


class Sp24HubTests(unittest.IsolatedAsyncioTestCase):
    async def test_mid_drain_overrun_marks_its_actual_page_boundary_without_mutating_source(self):
        hub=main.Hub(); client=Mock(); client.status.return_value=snapshot()
        first=batch([sample(1),sample(2,110)],cursor=2); first['tail']=6
        second=batch([sample(5,140),sample(6,150)],cursor=6)
        second.update(head=5,overrun=True)
        original=copy.deepcopy(second)
        client.waveform.side_effect=[first,second]
        with patch.object(main,'HostClient',return_value=client),patch.object(main,'load_config',return_value=dict(bridge_port=18371,firmware_variant='auto')),patch.object(main.time,'time',return_value=1000.12):
            await hub.poll_once()
        rows=list(hub.samples)
        self.assertEqual([row['sequence'] for row in rows],[1,2,5,6])
        self.assertEqual([row['gap_before'] for row in rows],[True,False,True,False])
        self.assertTrue(hub.latest['precision']['bridge_overrun'])
        self.assertEqual(second,original)

    async def test_hub_drains_pages_and_preserves_30_seconds_at_100hz(self):
        hub=main.Hub(); self.assertGreaterEqual(hub.samples.maxlen,3000)
        client=Mock(); client.status.return_value=snapshot()
        first=batch([sample(1)],cursor=1); first['tail']=2
        second=batch([sample(2,110)],cursor=2)
        client.waveform.side_effect=[first,second]
        with patch.object(main,'HostClient',return_value=client),patch.object(main,'load_config',return_value=dict(bridge_port=18371,firmware_variant='auto')),patch.object(main.time,'time',return_value=1000.12):
            await hub.poll_once()
        self.assertEqual(len(hub.samples),2)
        self.assertEqual(client.waveform.call_count,2)
        self.assertEqual(client.waveform.call_args_list[1].kwargs,dict(after=1,stream='fixture-stream'))

    async def test_preset_is_forwarded_once_and_readonly_assets_match_delivery(self):
        self.assertTrue(hasattr(main,'presets'),'GET /presets is required')
        values=main.presets(); self.assertEqual(len(values['presets']),6)
        self.assertIsInstance(values['engineering_profile'],dict)
        body=dict(action='apply-preset',request_id='preset-once',value=values['presets'][0])
        client=Mock(); client.status.return_value=snapshot(); client.command.return_value=dict(state='accepted',request_id='preset-once')
        with patch.object(main,'HostClient',return_value=client),patch.object(main,'load_config',return_value=dict(bridge_port=18371,firmware_variant='SP24')):
            result=await main.command(main.Command(**body)); template=main.profile_template()
        client.command.assert_called_once_with(body); self.assertEqual(result['state'],'accepted')
        self.assertEqual(json.loads(template.body),json.loads((ROOT/'host/sp24/gesture/host/machine_profile.template.json').read_text(encoding='utf-8-sig')))

    async def test_failed_page_does_not_advance_cursor_and_next_poll_replays_once(self):
        hub=main.Hub(); client=Mock(); client.status.return_value=snapshot()
        first=batch([sample(1)],cursor=1); first['tail']=2
        client.waveform.side_effect=[first,BridgeError('explicit fixture timeout'),first,batch([sample(2,110)],cursor=2)]
        with patch.object(main,'HostClient',return_value=client),patch.object(main,'load_config',return_value=dict(bridge_port=18371,firmware_variant='auto')),patch.object(main.time,'time',return_value=1000.12):
            await hub.poll_once()
            self.assertIsNone(hub.wave_cursor); self.assertEqual(len(hub.samples),0)
            await hub.poll_once()
        self.assertEqual(hub.wave_cursor,2); self.assertEqual(len(hub.samples),2)


if __name__=='__main__': unittest.main()
