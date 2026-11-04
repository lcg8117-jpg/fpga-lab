from pathlib import Path
import sys,threading,unittest
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'host'));sys.path.insert(0,str(Path(__file__).parent))
from test_stopped_upload import StoppedOwner
from draw_trajectory import upload,prepare,checksum
from protocol import RUN,ZERO,STOP,TRAJECTORY,SET_GAINS,FIRMWARE


class W1Tests(unittest.TestCase):
    def test_staging_all_nonmotion_modes_without_pose_or_control_sample_gate(self):
        for mode,fault in [(0,0),(1,0),(2,0),(3,0),(4,1),(4,4)]:
            owner=StoppedOwner();owner.mode=mode;owner.fault=fault;owner.fresh=False
            result=upload(owner,prepare([0,10,0]),threading.Event())
            self.assertTrue(owner.valid);self.assertEqual(owner.mode,mode);self.assertEqual(owner.fault,fault)
            self.assertFalse(any(c[0]in (RUN,ZERO,STOP,TRAJECTORY,SET_GAINS)for c in owner.calls))

    def test_one_click_upload_and_STOP_keep_completed_data(self):
        import tkinter as tk
        from tkinter import ttk
        from word_editor import WordEditor
        root=tk.Tk();root.withdraw();owner=StoppedOwner();owner.mode=4;owner.fault=1
        try:
            panel=WordEditor(ttk.Frame(root),lambda:owner);panel.upload_preset('GOWIN')
            panel.worker.join(timeout=3);self.assertFalse(panel.worker.is_alive())
            panel.after_cancel(panel._poll_job);panel._poll_job=None;panel.poll()
            expected=(owner,checksum(panel.points,1));self.assertEqual(panel.uploaded,expected)
            self.assertEqual(str(panel.play_button.cget('state')),'normal')
            calls=len(owner.calls);panel.abort_upload(send_abort=False)
            self.assertEqual(panel.uploaded,expected);self.assertIsNotNone(panel.word_plan)
            self.assertEqual(len(owner.calls),calls);self.assertTrue(owner.valid)
            self.assertFalse(any(c[0]in (RUN,ZERO,STOP,TRAJECTORY,SET_GAINS)for c in owner.calls));panel.destroy()
        finally:root.destroy()

    def test_matching_firmware_identity_required(self):self.assertEqual(FIRMWARE[5],0x56354336)


if __name__=='__main__':unittest.main()
