import unittest
from host.v4_r28_bridge import V4R28Bridge, install, load_upstream

class R28Tests(unittest.TestCase):
    def setUp(self):
        self.bridge = V4R28Bridge()
        self.client, self.p = load_upstream()
        install(self.bridge, self.client)
        self.owner = self.client.BLEOwner('00:00:00:00:00:00', '.')
        self.bridge.attach_owner(self.owner)
        self.owner.state.connected = True

    def auth(self):
        self.owner.pending[1] = (self.p.HELLO, 0, b'', 0)
        self.owner._receive(b'', dict(kind='ack', request_id=1, opcode=self.p.HELLO, status=0,
            stored_gains=[0]*6, stored_revision=0))
        self.owner._receive(b'', dict(kind='diagnostic', session=self.owner.session, board_ms=1, sequence=1))

    def sample(self, seq=1):
        return dict(kind='compact',wire_version=4,variant=4,sequence=seq,board_ms=seq*5,
                    control_epoch=seq*5,coherent=True,range_valid=True)

    def test_status_exposes_raw_receive_diagnostics_without_touching_transport(self):
        import threading
        from types import SimpleNamespace
        self.owner.parser.feed(b'non-frame-data')
        transport = SimpleNamespace(lock=threading.Lock(), startup_bytes=17,
            buffer=bytearray(b'xx'), pending_writes=2, error=None)
        self.owner.port = SimpleNamespace(port=transport)
        debug = self.bridge.status()['host']['link_diagnostics']
        self.assertEqual(debug['startup_rx_bytes'], 17)
        self.assertEqual(debug['ble_buffer_bytes'], 2)
        self.assertEqual(debug['pending_write_frames'], 2)
        self.assertEqual(debug['parser_discarded_bytes'], len(b'non-frame-data')-3)
        self.assertEqual(debug['parser_buffer_bytes'], 3)
        self.assertIsNone(debug['transport_error'])
        self.assertEqual(bytes(transport.buffer), b'xx')

    def test_disconnected_status_keeps_unknown_diagnostics_unknown(self):
        debug = self.bridge.status()['host']['link_diagnostics']
        self.assertIsNone(debug['startup_rx_bytes'])
        self.assertIsNone(debug['ble_buffer_bytes'])
        self.assertIsNone(debug['pending_write_frames'])

    def test_authentication_and_distinct_frames(self):
        self.owner._receive(b'', self.sample())
        self.assertEqual(self.bridge.waveform()['samples'], [])
        self.auth()
        self.owner._receive(b'', self.sample())
        page=self.bridge.waveform()
        self.assertEqual([r['frame']['kind'] for r in page['samples']], ['diagnostic','compact'])
        self.assertEqual(page['samples'][-1]['session'],self.bridge.status()['session'])
        self.assertNotIn('firmware_id',page['samples'][-1]['frame'])
        self.owner._receive(b'',self.sample())
        self.assertEqual(len(self.bridge.waveform()['samples']),2)

    def test_d_wrong_session_cannot_authenticate(self):
        self.owner.pending[1]=(self.p.HELLO,0,b'',0)
        self.owner._receive(b'',dict(kind='ack',request_id=1,opcode=self.p.HELLO,status=0,stored_gains=[0]*6,stored_revision=0))
        self.owner._receive(b'',dict(kind='diagnostic',session=self.owner.session+1,board_ms=1,sequence=1))
        self.owner._receive(b'',self.sample())
        self.assertEqual(self.bridge.waveform()['samples'],[])

    def test_raw_identity_frames_bind_only_matching_schema_and_session(self):
        import struct
        ack = bytearray(80)
        ack[:8] = b'FD2A' + bytes([4, self.p.HELLO, 0, 4])
        struct.pack_into('<I', ack, 8, 1)
        struct.pack_into('<I', ack, 72, self.p.FW)
        self.owner.pending[1] = (self.p.HELLO, 0, b'', 0)
        raw = self.p.seal(ack)
        self.owner._receive(raw, self.p.decode(raw))
        diagnostic = bytearray(256)
        diagnostic[:8] = b'FD2D' + bytes([4, 4, self.p.LAYOUT, 0])
        struct.pack_into('<II', diagnostic, 16, self.p.SCHEMA + 1, self.p.FW)
        struct.pack_into('<I', diagnostic, 164, self.owner.session)
        with self.assertRaises(ValueError): self.p.decode(self.p.seal(diagnostic))
        self.assertFalse(self.owner.state.authenticated)
        struct.pack_into('<I', diagnostic, 16, self.p.SCHEMA)
        raw = self.p.seal(diagnostic)
        self.owner._receive(raw, self.p.decode(raw))
        self.assertTrue(self.owner.state.authenticated)

    def test_once_only_and_removed_actions(self):
        self.auth()
        body=dict(request_id='a',action='stop')
        self.bridge.command(body); self.bridge.command(body)
        self.assertEqual(self.owner.queue.qsize(),1)
        with self.assertRaises(ValueError): self.bridge.command(dict(request_id='a',action='balance'))
        with self.assertRaises(ValueError): self.bridge.command(dict(request_id='b',action='start-swing'))
        with self.assertRaises(ValueError): self.bridge.command(dict(request_id='b',action='trajectory',value=1))


    def test_stop_marks_cancelled_queued_action(self):
        self.auth()
        self.owner.state.latest = dict(mode=3,fault=0,coherent=True,range_valid=True)
        import time
        self.owner.state.latest_rx = time.monotonic()
        self.bridge.command(dict(request_id='position',action='position',value=2))
        self.bridge.command(dict(request_id='stop',action='stop'))
        self.assertEqual(self.bridge.requests['position']['state'],'rejected')
        self.assertEqual(self.owner.queue.qsize(),1)


    def test_wire_ack_matches_id_and_opcode(self):
        self.auth()
        self.bridge.command(dict(request_id='stop', action='stop'))
        import threading
        self.owner.thread=threading.current_thread()
        self.owner.port=type('Port',(),{'write':lambda self, data: len(data)})()
        _,_,opcode,group,payload=self.owner.queue.get_nowait()
        wire=self.owner._send(opcode,group,payload)
        self.assertEqual(self.bridge.requests['stop']['state'],'pending')
        self.owner._receive(b'',dict(kind='ack',request_id=wire,opcode=self.p.STATUS,status=0,stored_gains=[0]*6,stored_revision=0))
        self.assertEqual(self.bridge.requests['stop']['state'],'pending')
        self.owner._receive(b'',dict(kind='ack',request_id=wire,opcode=opcode,status=0,stored_gains=[0]*6,stored_revision=0))
        self.assertEqual(self.bridge.requests['stop']['state'],'accepted')

    def test_skipped_dequeued_request_cannot_claim_heartbeat(self):
        import threading, time
        self.auth()
        self.owner.state.latest = dict(mode=3, fault=0, coherent=True, range_valid=True)
        self.owner.state.latest_rx = time.monotonic()
        self.bridge.command(dict(request_id='move', action='position', value=2))
        self.owner.thread = threading.current_thread()
        self.owner.port = type('Port', (), {'write': lambda self, data: len(data)})()
        self.owner.queue.get_nowait()  # worker dequeued; authentication then failed
        self.owner.state.authenticated = False
        wire = self.owner._send(self.p.STATUS)
        self.owner._receive(b'', dict(kind='ack', request_id=wire, opcode=self.p.STATUS,
            status=0, stored_gains=[0]*6, stored_revision=0))
        self.assertEqual(self.bridge.requests['move']['state'], 'rejected')
        self.assertNotIn('wire_request_id', self.bridge.requests['move'])

    def test_stop_retains_earlier_safe_request_identity(self):
        self.auth()
        self.bridge.command(dict(request_id='stop1', action='stop'))
        self.bridge.command(dict(request_id='stop2', action='stop'))
        self.assertEqual(self.bridge.requests['stop1']['state'], 'queued')
        import threading
        self.owner.thread = threading.current_thread()
        self.owner.port = type('Port', (), {'write': lambda self, data: len(data)})()
        _, _, opcode, group, payload = self.owner.queue.get_nowait()
        wire = self.owner._send(opcode, group, payload)
        self.assertEqual(self.bridge.requests['stop1'].get('wire_request_id'), wire)

    def test_owner_change_during_write_does_not_bind_old_wire(self):
        import threading
        self.auth()
        self.bridge.command(dict(request_id='old', action='stop'))
        self.owner.thread = threading.current_thread()
        old = self.owner
        bridge = self.bridge
        class Port:
            def write(self, data):
                bridge.attach_owner(self.new_owner)
                return len(data)
        port = Port()
        # Construct without the installed __init__ wrapper to control the switch.
        other = object.__new__(self.client.BLEOwner)
        self.client.BLEOwner._bridge_originals[0](other, '00:00:00:00:00:01', '.')
        port.new_owner = other
        old.port = port
        _, _, opcode, group, payload = old.queue.get_nowait()
        wire = old._send(opcode, group, payload)
        self.assertEqual(self.bridge.requests['old']['state'], 'indeterminate')
        self.assertNotIn(wire, self.bridge.wire_requests)

    def test_status_does_not_mix_old_owner_with_new_session(self):
        other = object.__new__(self.client.BLEOwner)
        self.client.BLEOwner._bridge_originals[0](other, '00:00:00:00:00:01', '.')
        snapshot = self.owner.state.snapshot
        def switch_owner():
            result = snapshot()
            self.bridge.attach_owner(other)
            return result
        self.owner.state.snapshot = switch_owner
        status = self.bridge.status()
        self.assertEqual(status['session'], str(other.session))
        self.assertFalse(status['connected'])

    def test_pid_requires_fresh_stopped_state_and_zero_integrals(self):
        self.auth()
        bad=self.bridge.command(dict(request_id='pid1',action='set-gains',value=[1,0,1,1,0,1]))
        self.assertEqual(bad['state'],'rejected')
        with self.assertRaises(ValueError): self.bridge.command(dict(request_id='pid2',action='set-gains',value=[1,1,1,1,0,1]))
        import time
        self.owner.state.last_ack=dict(mode=0,fault=0,flags=0)
        self.owner.state.last_ack_rx=time.monotonic()
        self.assertEqual(self.bridge.command(dict(request_id='pid3',action='set-gains',value=[1,0,1,1,0,1]))['state'],'queued')
