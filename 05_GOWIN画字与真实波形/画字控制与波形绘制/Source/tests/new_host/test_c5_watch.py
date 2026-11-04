"""Watchdog control tests with fake children; never starts a real compiler."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import importlib.util,json,sys,tempfile,unittest
O=Path(__file__).resolve().parents[2];R=O.parent
spec=importlib.util.spec_from_file_location('c5_watch',O/'tools/remote_compile_c5.py');watch=importlib.util.module_from_spec(spec);spec.loader.exec_module(watch)

class WatchTests(unittest.TestCase):
    def case(self,kind):
        parent=R/'C5_WATCH_TEST_20261029';parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='case_',dir=parent)as temporary:
            work=Path(temporary)/'native';calls=[]
            class Child:
                pid=123456;returncode=None;polls=0
                def poll(self):
                    self.polls+=1
                    if kind!='timeout' and self.polls>=2:self.returncode=0;return 0
                    return None
                def wait(self,timeout=None):self.returncode=-1;return -1
            child=Child()
            def spawn(argv,**kwargs):
                record=dict(status='WAITING_OTHER_COMPILER'if kind=='queue'else'RUNNING',compiler_pid=None if kind=='queue'else 654321,
                    usercode_hex='56354335',accepted=kind=='late')
                if kind!='queue':record.update(started_unix=100.,finished_unix=1300.1)
                (work/'STATUS.json').write_text(json.dumps(record),encoding='utf8');return child
            def stop(argv,**kwargs):calls.append(argv);return SimpleNamespace(stdout=b'fake stop',stderr=b'',returncode=0)
            argv=['watch','--compiler','D:/Gowin/Gowin_V1.9.12.03_x64/IDE/bin/gw_sh.exe','--python','D:/Codex_Scratch/J280_AutoReturn_Stiction_Field_20261028_R1/pc/runtime/python.exe','--workroot',str(work)]
            with patch.object(watch,'verify',return_value={}),patch.object(watch.subprocess,'Popen',side_effect=spawn),patch.object(watch.subprocess,'run',side_effect=stop),patch.object(watch.time,'time',return_value=1400. if kind=='timeout'else 200.),patch.object(watch.time,'sleep'),patch.object(sys,'argv',argv):
                result=watch.main()
            return result,json.loads((work/'REMOTE_WATCH_RESULT.json').read_text()),calls
    def test_running_timeout_stops_only_own_process_tree(self):
        code,record,calls=self.case('timeout');self.assertEqual(code,2);self.assertFalse(record['accepted']);self.assertEqual(record['status'],'TIMEOUT_NOT_ACCEPTED')
        self.assertEqual(calls,[['taskkill','/PID','123456','/T','/F']])
    def test_queue_wait_does_not_consume_running_limit_or_kill_anyone(self):
        code,record,calls=self.case('queue');self.assertEqual(record['running_seconds'],0);self.assertFalse(calls);self.assertFalse(record['accepted'])
    def test_finished_after_twenty_minutes_is_never_accepted(self):
        code,record,calls=self.case('late');self.assertFalse(record['accepted']);self.assertEqual(record['status'],'TIMEOUT_NOT_ACCEPTED');self.assertFalse(calls)

if __name__=='__main__':unittest.main()
