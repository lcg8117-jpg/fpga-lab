"""Offline checks for the new profile path; the fixture opens no COM port."""
import copy
import unittest
from test_tune_host import Fixture

PROFILE=dict(schema='J280.E1993.profile.v1',sample_seconds=.010,Ku=1.0,Tu_seconds=1.0,
 error_nodes_rad=[1,2,3],delta_nodes_rad_per_sample=[1,2,3],
 provenance=dict(kind='synthetic',sources=['unit-test fixture only'],control_qualified=False))


class ProfileTests(unittest.TestCase):
    def test_synthetic_cannot_be_control_qualified(self):
        f=Fixture();r=f.model.submit('profile','load-profile',value=dict(profile=PROFILE,control=True))
        self.assertEqual(r['state'],'rejected');self.assertIsNone(f.model.batch)
        self.assertFalse(any(x[0][18]==0x70 for x in f.sent))

    def test_shadow_upload_is_readback_verified_and_stays_off(self):
        f=Fixture();f.model.submit('profile','load-profile',value=dict(profile=PROFILE,control=False))
        for _ in range(300):
            if f.model.requests['profile']['state']=='verified':break
            if f.model.pending:f.answer()
            else:f.tick()
        self.assertEqual(f.model.requests['profile']['state'],'verified')
        writes=[packet for packet,_,_ in f.sent if packet[18]==0x70]
        self.assertEqual(len(writes),13)
        self.assertEqual(writes[0][19],0x31);self.assertEqual(writes[0][20:24],bytes(4))
        self.assertEqual(writes[-1][19],0x33);self.assertEqual(int.from_bytes(writes[-1][20:24],'little'),2)
        self.assertEqual(f.values[0x31],0)
        self.assertFalse(any(packet[18] in (2,5) for packet,_,_ in f.sent))

    def test_bad_nodes_reject_before_any_write(self):
        f=Fixture();profile=copy.deepcopy(PROFILE);profile['error_nodes_rad']=[2,1,3]
        r=f.model.submit('bad','load-profile',value=dict(profile=profile,control=False))
        self.assertEqual(r['state'],'rejected');self.assertIsNone(f.model.batch)

    def test_stop_cancels_remaining_profile_and_does_not_enable(self):
        f=Fixture();f.model.submit('profile','load-profile',value=dict(profile=PROFILE,control=False))
        f.tick();f.answer();f.answer()
        f.model.submit('stop','stop');f.answer()
        for _ in range(30):
            if f.model.pending:f.answer()
            else:f.tick()
        self.assertIsNone(f.model.batch)
        self.assertEqual(f.model.requests['profile']['state'],'cancelled')
        self.assertFalse(any(packet[18] in (2,5) for packet,_,_ in f.sent))


if __name__=='__main__':unittest.main()
