from pathlib import Path
import sys,unittest
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'host'))
from word_progress import progress,coverage,return_status


class ProgressTests(unittest.TestCase):
    meta=dict(word_start_ms=20,word_end_ms=40)

    def rows(self,end):return [(11+i,i*5,i,6,3,0,True)for i in range(end//5+1)]

    def state(self):
        return dict(control_fresh=True,last_control=dict(mode=3,fault=0,motor_permitted_at_completion=True),
            latest_diagnostic=dict(motion_request_id=77,last_completed_epoch=20,trajectory_done=True,tracking_settled=True),last_diagnostic_rx=100.)

    def test_phase_uses_board_time(self):
        self.assertEqual(progress([],self.meta,10)['stage'],'waiting')
        self.assertEqual(progress(self.rows(15),self.meta,10)['stage'],'pre_motion')
        self.assertEqual(progress(self.rows(30),self.meta,10)['stage'],'writing')
        self.assertTrue(progress(self.rows(40),self.meta,10)['ended'])

    def test_complete_requires_all_exact_time_slots(self):
        full=[(i,1,.01)for i in range(20,40,5)]
        self.assertTrue(coverage(full,self.meta)['complete'])
        self.assertFalse(coverage(full[:-1],self.meta)['complete'])
        self.assertFalse(coverage([full[0],None,*full[2:]],self.meta)['complete'])

    def test_same_request_fresh_settled_required(self):
        s=self.state();self.assertEqual(return_status(s,77,10,now=100.1)[0],'settled')
        self.assertEqual(return_status(s,78,10,now=100.1)[0],'other')
        self.assertEqual(return_status(s,77,10,now=101)[0],'waiting')
        s['latest_diagnostic']['last_completed_epoch']=9
        self.assertEqual(return_status(s,77,10,now=100.1)[0],'stale')

    def test_reference_done_alone_does_not_mean_actually_settled(self):
        s=self.state();s['latest_diagnostic']['tracking_settled']=False
        self.assertEqual(return_status(s,77,10,now=100.1)[0],'settling')
        s['latest_diagnostic']['trajectory_done']=False
        self.assertEqual(return_status(s,77,10,now=100.1)[0],'returning')

    def test_stop_fault_and_expired_control_cannot_pass(self):
        for key,value in [('mode',1),('fault',1),('motor_permitted_at_completion',False)]:
            s=self.state();s['last_control'][key]=value
            self.assertEqual(return_status(s,77,10,now=100.1)[0],'interrupted')
        s=self.state();s['control_fresh']=False
        self.assertEqual(return_status(s,77,10,now=100.1)[0],'stale')
        r=self.rows(30);r[-1]=(17,30,0,6,1,0,False)
        self.assertEqual(progress(r,self.meta,10)['stage'],'interrupted')
        # A smooth cancel may keep balance active; it still did not finish
        # the requested word and must never become a completed drawing.
        r=self.rows(30);r[-1]=(17,30,0,3,3,0,True)
        self.assertEqual(progress(r,self.meta,10)['stage'],'interrupted')

    def test_latched_onset_is_not_shifted_after_ring_rollover(self):
        r=[(1000,5000,0,0,3,0,True)]
        self.assertEqual(progress(r,self.meta,10,origin_ms=0)['origin_ms'],0)
        self.assertTrue(progress(r,self.meta,10,origin_ms=0)['ended'])

    def test_ui_preserves_completed_trace_after_history_rollover(self):
        import tkinter as tk
        from tkinter import ttk
        from types import SimpleNamespace
        import threading,time
        from word_editor import WordEditor
        root=tk.Tk();root.withdraw();state=self.state()
        state['word_history']=self.rows(45);state['last_diagnostic_rx']=time.monotonic()
        owner=SimpleNamespace(state=SimpleNamespace(lock=threading.RLock(),command_results={}))
        owner.state.snapshot=lambda:state
        try:
            panel=WordEditor(ttk.Frame(root),lambda:owner)
            panel.word_plan=dict(word_metadata={**self.meta,'window_ms':10})
            panel.play_epoch=10;panel.play_owner=owner;panel.play_request_id=77
            panel.after_cancel(panel._poll_job);panel._poll_job=None;panel.poll()
            self.assertTrue(panel.trace_frozen);saved=list(panel.actual)
            self.assertTrue(panel.data_coverage['complete'])
            self.assertIn('已回到本次起点并停稳',panel.status.get())
            state['word_history']=[(1000,5000,0,0,3,0,True)]
            panel.after_cancel(panel._poll_job);panel._poll_job=None;panel.poll()
            self.assertEqual(saved,panel.actual);self.assertEqual(panel.origin_ms,0)
            panel.destroy()
        finally:root.destroy()


if __name__=='__main__':unittest.main()
