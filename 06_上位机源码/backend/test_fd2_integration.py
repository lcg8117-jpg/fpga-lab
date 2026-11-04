"""Original log -> original codec -> bridge HTTP -> actual Hub, entirely offline."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

import main
from host.fd2_bridge import Bridge, load_upstream
from host.fd2_replay import Replay
from host_bridge import BridgeError, HostClient
from test_fd2_bridge import native_frame


def write_offline_log(path, variant, count=201):
    modules = load_upstream()
    protocol, header = modules['protocol'], modules['trace_log'].CHUNK
    chunks = []
    # A historical TX RUN is deliberately included; replay must never execute it.
    tx = protocol.request(protocol.RUN, 1, version=protocol.WIRE_VERSIONS[variant])
    chunks.append(header.pack(1, 1, 0, 1, len(tx), zlib.crc32(tx)) + tx)
    for i in range(1, count+1):
        raw = bytearray(native_frame(variant=variant, sequence=i, board_ms=i*5))
        raw[6] = 119 if variant == 3 else 23
        raw[7] = 3
        struct.pack_into('<H', raw, 54, 3086)
        struct.pack_into('<h', raw, 62, -8192)
        struct.pack_into('<q', raw, 72, 1040+i)
        struct.pack_into('<q', raw, 80, 2080)
        struct.pack_into('<H', raw, 112, 3085)
        for offset in (128, 152, 176):
            struct.pack_into('<6f', raw, offset, 49, 0, 251, 25, 0, 610)
        struct.pack_into('<H', raw, 254, protocol.crc16(raw[:-2]))
        chunks.append(header.pack(i*5_000_000, i*5_000_000, 0, 0, len(raw), zlib.crc32(raw))+raw)
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('session.json', json.dumps(dict(format='ForestFive.Physical.v1', variant=variant, synthetic_offline_test=True)))
        archive.writestr('physical_chunks.bin', b''.join(chunks))


class FD2IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_variants_replay_through_http_and_real_hub_without_hardware(self):
        for variant in range(1, 6):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'EXPLICIT_SYNTHETIC_OFFLINE_TEST.zip'
                write_offline_log(path, variant)
                bridge = Bridge(variant, replay=True)
                server = bridge.serve(0)
                port = server.server_port
                try:
                    with patch.object(load_upstream()['client'].SerialOwner, 'start', side_effect=AssertionError('device thread forbidden')):
                        Replay(bridge, path, speed=10000).run()
                    hub = main.Hub()
                    with patch.object(main, 'load_config', return_value=dict(bridge_port=port, firmware_variant=f'V{variant}')):
                        await hub.poll_once()
                        self.assertIsNone(hub.last_error)
                        self.assertEqual(len(hub.samples), 201)
                        self.assertEqual(hub.latest['firmware'], f'V{variant}')
                        self.assertFalse(hub.latest['connected'])
                        self.assertFalse(hub.latest['status_fresh'])
                        self.assertTrue(hub.latest['display_fresh'])
                        self.assertEqual(hub.latest['duty_actual_percent'], -25)
                        if variant == 3:
                            self.assertTrue(hub.latest['adaptive']['averages']['valid'])
                        await hub.poll_once()
                        self.assertEqual(len(hub.samples), 201)
                    with self.assertRaises(BridgeError) as error:
                        HostClient(port).command(dict(action='stop', request_id='forbidden'))
                    self.assertTrue(error.exception.request_rejected)
                    self.assertFalse(bridge.requests)
                finally:
                    server.shutdown(); server.server_close()


if __name__ == '__main__': unittest.main()
