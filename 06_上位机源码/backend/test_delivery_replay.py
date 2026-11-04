import json, tempfile, unittest, zipfile
from pathlib import Path
from host.delivery_replay import ReplayBridge

class ReplayTests(unittest.TestCase):
    def archive(self, meta, name='physical_chunks.bin', data=b''):
        f=tempfile.NamedTemporaryFile(suffix='.zip',delete=False); f.close()
        self.addCleanup(Path(f.name).unlink)
        with zipfile.ZipFile(f.name,'w') as z:
            z.writestr('session.json',json.dumps(meta)); z.writestr(name,data)
        return f.name
    def test_snapshot_is_offline_and_partial(self):
        p=self.archive(dict(format='ForestFive.Physical.v2',variant=5,chunk_header='<QQBBII',snapshot=True,complete_session=False))
        b=ReplayBridge('G3_C1',p); b.start(); b.thread.join(2)
        s=b.status(); self.assertTrue(s['replay']['active']); self.assertTrue(s['replay']['partial']); self.assertFalse(s['connected'])
        with self.assertRaises(ValueError): b.command({'request_id':'1','action':'stop'})
    def test_v4_cannot_authenticate_rx_only(self):
        p=self.archive(dict(format='ForestD1.Raw.v2',variant=4),name='records.bin')
        with self.assertRaises(ValueError): ReplayBridge('V4_R28',p)
    def test_v4_replay_uses_only_self_identifying_diagnostics(self):
        import struct
        from host.delivery_replay import V4DiagnosticReplayBridge
        from host.v4_r28_bridge import load_upstream
        protocol=load_upstream()[1]
        diagnostic=bytearray(256);diagnostic[:8]=b'FD2D'+bytes([4,4,5,0])
        struct.pack_into('<IIII',diagnostic,8,1,1000,protocol.SCHEMA,protocol.FW)
        struct.pack_into('<I',diagnostic,164,42)
        struct.pack_into('<H',diagnostic,254,protocol.crc16(diagnostic[:-2]))
        compact=bytearray(48);compact[:8]=b'FD2B'+bytes([4,4,129,3])
        struct.pack_into('<H',compact,46,protocol.crc16(compact[:-2]))
        def record(raw):return struct.pack('<QQH',1000000000,1000000000,len(raw))+raw
        path=self.archive(dict(format='ForestD1.Raw.v2',variant=4,firmware='0x56345239',schema='0x56345208',wire_version=4,diagnostic_layout=5),name='records.bin',data=record(diagnostic)+record(compact))
        bridge=V4DiagnosticReplayBridge(path,speed=1000).start();bridge.thread.join(2)
        self.assertEqual([row['frame']['kind'] for row in bridge.waveform()['samples']],['diagnostic'])
        self.assertEqual(bridge.replay['unbound_compact'],1)
        self.assertTrue(bridge.replay['partial'])
        with self.assertRaises(ValueError):bridge.command({'action':'stop','request_id':'r'})
        struct.pack_into('<I',diagnostic,16,0x12345678)
        struct.pack_into('<H',diagnostic,254,protocol.crc16(diagnostic[:-2]))
        wrong=self.archive(dict(format='ForestD1.Raw.v2',variant=4,firmware='0x56345239',schema='0x56345208',wire_version=4,diagnostic_layout=5),name='records.bin',data=record(diagnostic))
        rejected=V4DiagnosticReplayBridge(wrong,speed=1000).start();rejected.thread.join(2)
        self.assertTrue(rejected.replay['error'])
        self.assertEqual(rejected.replay['crc_errors'],0)
    def test_launcher_replay_stays_offline(self):
        import subprocess,sys
        path=self.archive(dict(format='ForestFive.Physical.v2',variant=5,chunk_header='<QQBBII',snapshot=True))
        root=Path(__file__).resolve().parents[1]
        proc=subprocess.run([sys.executable,'-B','-X','utf8',str(root/'launch_host.py'),'G3_C1',
                             '--replay',path,'--headless','--once','--port','0'],cwd=root,
                            capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(proc.returncode,0,proc.stderr)
        status=json.loads(proc.stdout)
        self.assertTrue(status['replay']['active'])
        self.assertFalse(status['connected'])
    def test_new_v4_replay_accepts_only_3a_diagnostics(self):
        import struct
        from host.delivery_replay import V4DiagnosticReplayBridge
        from host.v4_usb_ble_bridge import load_upstream
        protocol=load_upstream()['protocol']
        def diagnostic(firmware):
            data=bytearray(256);data[:8]=b'FD2D'+bytes([4,4,5,0])
            struct.pack_into('<IIII',data,8,1,1000,protocol.SCHEMA,firmware)
            struct.pack_into('<I',data,164,42)
            return protocol.seal(data)
        def record(raw):return struct.pack('<QQH',1000000000,1000000000,len(raw))+raw
        meta=dict(format='ForestD1.Raw.v2',variant=4,firmware='0x5634523A',schema='0x56345208',wire_version=4,diagnostic_layout=5)
        valid=self.archive(meta,name='records.bin',data=record(diagnostic(0x5634523A)))
        bridge=V4DiagnosticReplayBridge(valid,speed=1000,profile='V4_USB_BLE').start();bridge.thread.join(2)
        self.assertEqual(bridge.status()['user_code'],'5634523A')
        self.assertEqual(len(bridge.waveform()['samples']),1)
        self.assertTrue(bridge.status()['replay']['active'])
        wrong=self.archive(meta,name='records.bin',data=record(diagnostic(0x56345239)))
        rejected=V4DiagnosticReplayBridge(wrong,speed=1000,profile='V4_USB_BLE').start();rejected.thread.join(2)
        self.assertTrue(rejected.replay['error'])
        self.assertEqual(rejected.waveform()['samples'],[])
        with self.assertRaises(ValueError):bridge.command({'request_id':'x','action':'stop'})
    def test_ds13_raw_receive_replay_is_read_only(self):
        import struct
        from host.ds13_bridge import load_upstream
        from host.delivery_replay import DS13ReplayBridge
        protocol=load_upstream()['protocol']
        data=bytearray(256);data[:8]=b'FD2T'+bytes([5,5,17,3])
        struct.pack_into('<III',data,8,1,5,5)
        struct.pack_into('<II',data,32,0x44533133,0x44535002)
        struct.pack_into('<HH',data,50,100,110)
        struct.pack_into('<H',data,254,protocol.crc16(data[:-2]))
        f=tempfile.NamedTemporaryFile(suffix='.bin',delete=False)
        f.write(data);f.close();self.addCleanup(Path(f.name).unlink)
        bridge=DS13ReplayBridge(f.name,speed=1000).start();bridge.thread.join(2)
        rows=bridge.waveform()['samples']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['frame']['quiet_q4'],110)
        self.assertTrue(bridge.status()['replay']['active'])
        with self.assertRaises(ValueError):bridge.command({'action':'stop','request_id':'x'})
    def test_profile_mismatch_rejected(self):
        p=self.archive(dict(format='ForestFive.Physical.v2',variant=4,chunk_header='<QQBBII'))
        with self.assertRaises(ValueError): ReplayBridge('G3_C1',p)
    def test_corrupt_wire_frame_is_skipped_and_next_sample_has_gap(self):
        import struct, zlib
        from host.g3_c1_bridge import load_upstream
        p=load_upstream()['protocol']
        def frame(seq):
            b=bytearray(256); b[:7]=b'FD2T'+bytes([5,5,1])
            struct.pack_into('<III',b,8,seq,seq*5,seq*5)
            struct.pack_into('<II',b,32,p.FIRMWARE[5],p.SCHEMAS[5])
            struct.pack_into('<H',b,254,p.crc16(b[:-2])); return bytes(b)
        bad=bytearray(frame(2)); bad[50]^=1
        def chunk(t,raw): return struct.pack('<QQBBII',t,t,0,0,len(raw),zlib.crc32(raw))+raw
        path=self.archive(dict(format='ForestFive.Physical.v2',variant=5,chunk_header='<QQBBII'),data=chunk(1,frame(1))+chunk(2,bad)+chunk(3,frame(3)))
        b=ReplayBridge('G3_C1',path,speed=1e9).start(); b.thread.join(2)
        rows=b.waveform()['samples']; self.assertEqual(len(rows),2)
        self.assertTrue(rows[1]['gap_before']); self.assertEqual(b.replay['crc_errors'],1)
    def test_bad_chunk_crc_marks_error_without_publishing(self):
        import struct
        data=struct.pack('<QQBBII',1,1,0,0,3,0)+b'bad'
        p=self.archive(dict(format='ForestFive.Physical.v2',variant=5,chunk_header='<QQBBII'),data=data)
        b=ReplayBridge('G3_C1',p,speed=1e9).start(); b.thread.join(2)
        self.assertTrue(b.replay['error']); self.assertTrue(b.replay['partial'])
        self.assertEqual(b.waveform()['samples'],[])
    def test_http_commands_rejected_and_status_offline(self):
        import http.client
        p=self.archive(dict(format='ForestFive.Physical.v2',variant=5,chunk_header='<QQBBII'))
        b=ReplayBridge('G3_C1',p); server=b.serve(0)
        try:
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
            c.request('POST','/command',body='{"request_id":"1","action":"stop"}',headers={'X-J280-Local':'1','Content-Type':'application/json'})
            r=c.getresponse(); self.assertEqual(r.status,400); r.read()
            c.request('GET','/status',headers={'X-J280-Local':'1'})
            r=c.getresponse(); s=json.loads(r.read()); self.assertTrue(s['replay']['active']); self.assertFalse(s['connected'])
            c.close()
        finally: b.stop()
