from pathlib import Path
import sys,threading,unittest,time
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'host'));sys.path.insert(0,str(Path(__file__).parent))
from test_draw import FakeOwner
from protocol import STATUS,STOP,RUN,ZERO,TRAJECTORY,WRITE_CONFIG,SET_GAINS
from draw_trajectory import upload,prepare,checksum


class StoppedOwner(FakeOwner):
    def __init__(self):
        super().__init__();self.mode=1;self.fault=0;self.point_reject=False
    def command(self,*args,**kw):
        ticket=super().command(*args,**kw);ack=self.state.command_results[ticket]
        ack.update(mode=self.mode,fault=self.fault,flags=0 if self.mode in (0,1) else 4)
        if args[0]==WRITE_CONFIG and args[2][0]==1 and self.point_reject:ack['status']=1
        return ticket


class StopUploadTests(unittest.TestCase):
    def test_stopped_upload_works_without_RUN_and_without_fresh_balance(self):
        owner=StoppedOwner();owner.fresh=False
        result=upload(owner,prepare([0,10,0]),threading.Event())
        self.assertTrue(owner.valid);self.assertTrue(result['uploaded_while_stopped'])
        self.assertFalse(any(c[0] in (STOP,RUN,ZERO,TRAJECTORY,SET_GAINS)for c in owner.calls))

    def test_pose_fault_does_not_block_staging_or_get_cleared(self):
        owner=StoppedOwner();owner.mode=4;owner.fault=1
        result=upload(owner,prepare([0,10,0]),threading.Event())
        self.assertTrue(owner.valid);self.assertEqual(owner.fault,1)
        self.assertFalse(any(c[0]in (STOP,RUN,ZERO,TRAJECTORY,SET_GAINS)for c in owner.calls))

    def test_known_point_rejection_identifies_point_and_never_commits(self):
        owner=StoppedOwner();owner.point_reject=True
        with self.assertRaisesRegex(RuntimeError,r'POINT\[0\]'):upload(owner,prepare([0,10,0]),threading.Event())
        writes=[c for c in owner.calls if c[0]==WRITE_CONFIG]
        self.assertEqual([c[2][0]for c in writes],[7,0,1])
        self.assertFalse(any(c[0]==TRAJECTORY for c in owner.calls))

    def test_ui_enables_play_only_after_complete_stopped_upload(self):
        import tkinter as tk
        from tkinter import ttk
        from word_editor import WordEditor
        root=tk.Tk();root.withdraw();owner=StoppedOwner()
        try:
            panel=WordEditor(ttk.Frame(root),lambda:owner);panel.generate_word('GOWIN')
            self.assertEqual(str(panel.play_button.cget('state')),'disabled')
            panel.start_upload();panel.worker.join(timeout=3);self.assertFalse(panel.worker.is_alive())
            panel.after_cancel(panel._poll_job);panel._poll_job=None;panel.poll()
            self.assertEqual(str(panel.play_button.cget('state')),'normal')
            self.assertEqual(panel.uploaded,(owner,checksum(panel.points,1)))
            self.assertIsNotNone(panel.word_plan);self.assertIn('手动扶直ZERO/RUN',panel.play_hint.get())
            self.assertFalse(any(c[0]==TRAJECTORY for c in owner.calls));panel.destroy()
        finally:root.destroy()


if __name__=='__main__':unittest.main()
