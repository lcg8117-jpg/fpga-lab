"""Exercise rejected inputs and identity isolation through the real queue boundary."""
from pathlib import Path
import struct
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'host'))
from client import SerialOwner
from protocol import POSITION,WRITE_CONFIG,STOP,FIRMWARE
from trajectory_settings import validate

class TrajectoryBounds(unittest.TestCase):
    def owner(self):
        owner=SerialOwner(5,'OFFLINE_NEVER_OPENED',ROOT/'unused')
        owner.state.connected=True;owner.state.authenticated=True
        return owner

    def test_rejected_settings_never_enter_transport_queue(self):
        owner=self.owner()
        for field,value in [(0,0),(0,59),(0,261),(1,640),(2,3200),(6,2),
                            (7,0),(7,417),(7,2**32-1),(8,4000),(8,15999),(9,0),(9,3),(255,1)]:
            with self.subTest(field=field,value=value):
                with self.assertRaises(ValueError):owner.command(WRITE_CONFIG,253,bytes((field,))+struct.pack('<I',value))
                self.assertTrue(owner.queue.empty())

    def test_wire_length_and_position_commands_cannot_bypass_bounds(self):
        owner=self.owner()
        for payload in [b'',bytes(4),bytes(6)]:
            with self.assertRaises(ValueError):owner.command(WRITE_CONFIG,253,payload)
        with self.assertRaises(ValueError):owner.command(POSITION,payload=b'\x06')
        self.assertTrue(owner.queue.empty())
        owner.command(STOP);self.assertEqual(owner.queue.qsize(),1)

    def test_default_and_readable_degrees_are_consistent(self):
        for field,value in [(0,260),(1,320),(2,1600),(6,1),(7,416),(8,24000),(9,2)]:
            self.assertEqual(validate(field,value),value)
        for value in [True,192.0,float('nan'),float('inf'),'192']:
            with self.assertRaises(ValueError):validate(7,value)
        self.assertEqual(2*416*360/1040,288)

    def test_revised_firmware_has_a_distinct_identity(self):
        self.assertEqual(FIRMWARE[5],0x56354335)
        self.assertNotEqual(FIRMWARE[5],0x46443435)

if __name__=='__main__':unittest.main()
