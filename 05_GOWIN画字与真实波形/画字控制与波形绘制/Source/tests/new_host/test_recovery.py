"""Regress transient recovery without touching a port."""
from pathlib import Path
import importlib.util
import sys,time,unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'host'))
spec=importlib.util.spec_from_file_location('old_host_fixtures',ROOT/'tests/host/test_host.py')
fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
from client import SerialOwner
from protocol import STATUS,decode
STALE='超过500ms未收到有效新帧，控制已锁定'
class Recovery(unittest.TestCase):
 def owner(self,error):
  owner=SerialOwner(2,'OFFLINE',ROOT/'unused');owner.state.connected=True;owner.state.error=error;owner.pending[1]=(STATUS,0,b'',time.monotonic())
  return owner
 def test_matching_status_clears_only_temporary_stale_error(self):
  owner=self.owner(STALE);owner._receive(fixtures.ack(variant=2),decode(fixtures.ack(variant=2)))
  self.assertTrue(owner.state.authenticated);self.assertEqual(owner.state.error,'')
 def test_matching_status_preserves_unknown_write(self):
  error='命令结果未知：不会自动重发，请独立读回确认';owner=self.owner(error)
  owner._receive(fixtures.ack(variant=2),decode(fixtures.ack(variant=2)));self.assertEqual(owner.state.error,error)
 def test_real_identity_failure_is_not_cleared(self):
  owner=self.owner('固件/版本/schema不匹配，禁止控制');owner._identity_failed=True
  owner._receive(fixtures.ack(variant=2),decode(fixtures.ack(variant=2)));self.assertFalse(owner.state.authenticated);self.assertNotEqual(owner.state.error,'')
 def test_status_does_not_fabricate_control_freshness(self):
  owner=self.owner(STALE);owner._receive(fixtures.ack(variant=2),decode(fixtures.ack(variant=2)))
  self.assertFalse(owner.state.snapshot()['control_fresh'])
 def test_log_error_survives_status_recovery(self):
  owner=self.owner(STALE);owner.state.log_error='disk unavailable'
  owner._receive(fixtures.ack(variant=2),decode(fixtures.ack(variant=2)));self.assertEqual(owner.state.log_error,'disk unavailable')
if __name__=='__main__':unittest.main()
