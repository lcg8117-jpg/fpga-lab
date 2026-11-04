"""Offline-only contract, measurement integrity, upload and hidden UI tests."""
from pathlib import Path
import sys,math,threading,unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'host'));sys.path.insert(0,str(Path(__file__).parent))
from word_trajectory import generate,measured_trace,_integer_program
from draw_trajectory import validate,upload,checksum,clocked_curve
from test_draw import FakeOwner
from protocol import STOP,RUN,ZERO,TRAJECTORY,SET_GAINS


class Words(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.programs={w:generate(w) for w in ('GOWIN','EasyStart')}

    def test_finite_program_and_return_contract(self):
        for w,p in self.programs.items():
            pts=p['points'];m=p['word_metadata'];validate(pts)
            self.assertLessEqual(len(pts),512);self.assertLessEqual(m['total_s'],102)
            self.assertEqual(pts[:1]+pts[-2:],[0,0,0]);self.assertEqual(checksum(pts,1),m['checksum'])
            self.assertEqual(m['total_s'],(len(pts)-3)*.2);self.assertEqual(p['tracking_mode'],1)
            self.assertLessEqual(m['max_integer_projection_error_count'],m['integer_projection_bound_count'])
            self.assertLessEqual(m['travel_count'],1040)
            self.assertLess(m['word_end_ms'],m['total_s']*1000)

    def test_target_coordinates_use_integer_schedule_only(self):
        for p in self.programs.values():
            m=p['word_metadata'];start=m['word_start_ms']//5;lag=m['window_ms']//5;curve=clocked_curve(p['points'],1)
            for j,(x,y) in enumerate(p['target_only_xy']):
                i=start+j;self.assertEqual(x,curve[i]/65536)
                self.assertAlmostEqual(y,(curve[i]-curve[i-lag])/65536*2*math.pi/1040/(lag*.005))

    def test_only_input_upload_no_motor_or_gain_commands(self):
        for p in self.programs.values():
            fake=FakeOwner();result=upload(fake,p['points'],threading.Event(),tracking_mode=1)
            self.assertTrue(fake.valid);self.assertEqual(result['checksum'],p['word_metadata']['checksum'])
            self.assertFalse(any(c[0] in (STOP,RUN,ZERO,TRAJECTORY,SET_GAINS) for c in fake.calls))

    def test_projection_is_bounded_and_rejects_impossible(self):
        self.assertEqual(_integer_program([0,0,1,1,0,0]),[0,0,1,1,0,0])
        with self.assertRaises(ValueError):_integer_program([0,100,0,0])
        with self.assertRaises(ValueError):generate('other')

    def test_log_caps_cannot_be_raised_by_options(self):
        from trace_log import TraceLogger
        logger=TraceLogger(ROOT/'unused',5,max_shared_bytes=8*1024**3,max_session_bytes=1024**3)
        self.assertEqual(logger.max_shared_bytes,1024**3)
        self.assertEqual(logger.max_session_bytes,256*1024**2)
        logger.logical_bytes=126*1024**2
        with self.assertRaises(OSError):logger._check_session(1)

    def fixture(self,*,wrap=False):
        p=self.programs['GOWIN'];m=p['word_metadata'];origin=0xfffffff0 if wrap else 0
        rows=[(10+i,(origin+i*5)&0xffffffff,i,6,3,0,True) for i in range(m['word_end_ms']//5+2)]
        return rows,m

    def test_measured_axes_are_actual_data_not_target_font(self):
        rows,m=self.fixture();out=measured_trace(rows,m,9)
        self.assertTrue(out)
        for dt,x,y in out:
            self.assertEqual(x,dt//5)
            self.assertAlmostEqual(y,(m['window_ms']/5)*2*math.pi/1040/(m['window_ms']/1000))
        # Synthetic actual motion is a ramp. It must remain a ramp regardless
        # of the requested GOWIN path; cannot turn it into font geometry.
        self.assertEqual(len(set(round(p[2],12) for p in out)),1)

    def test_clock_wrap_uses_exact_pairs(self):
        rows,m=self.fixture(wrap=True);self.assertEqual(measured_trace(rows,m,9),measured_trace(self.fixture()[0],m,9))

    def test_gap_preserves_exact_endpoints_and_is_not_joined(self):
        rows,m=self.fixture();missing=m['word_start_ms']-m['window_ms']+100;del rows[missing//5]
        out=measured_trace(rows,m,9)
        self.assertTrue(any(p is None for p in out))
        valid=[p for p in out if p is not None]
        self.assertTrue(valid);self.assertEqual(valid[0][0],m['word_start_ms'])
        # An absent pre-motion endpoint creates a hole inside the word window.
        self.assertNotIn(missing+m['window_ms'],[p[0] for p in valid])

    def test_stop_and_wrong_ack_cannot_become_live_word(self):
        rows,m=self.fixture();r=list(rows[100]);r[4]=1;rows[100]=tuple(r)
        self.assertEqual(measured_trace(rows,m,9),[])
        self.assertEqual(measured_trace(self.fixture()[0],m,100000),[])

    def test_hidden_ui_build_generate_and_invalidate(self):
        import tkinter as tk
        from tkinter import ttk
        from word_editor import WordEditor
        root=tk.Tk();root.withdraw()
        try:
            panel=WordEditor(ttk.Frame(root),lambda:None)
            for word in self.programs:
                panel.generate_word(word)
                self.assertEqual(panel.word_plan['word_metadata']['word'],word)
                self.assertIsNone(panel.play_epoch);self.assertFalse(panel.actual)
                self.assertEqual(str(panel.play_button.cget('state')),'disabled')
                panel.play();self.assertIsNone(panel.play_pending)
            panel.invalidate();self.assertIsNone(panel.word_plan)
            root.update_idletasks();self.assertFalse(root.winfo_ismapped());panel.destroy()
        finally:root.destroy()

    def test_ui_requires_explicit_play_and_matched_ack(self):
        import tkinter as tk
        from tkinter import ttk
        from word_editor import WordEditor
        root=tk.Tk();root.withdraw();owner=FakeOwner()
        owner.state.snapshot=lambda:dict(control_fresh=True,word_history=[])
        try:
            panel=WordEditor(ttk.Frame(root),lambda:owner);panel.generate_word('GOWIN')
            self.assertEqual(owner.calls,[])
            panel.uploaded=(owner,checksum(panel.points,1));panel.play()
            self.assertEqual(len(owner.calls),1);self.assertEqual(owner.calls[0][0],TRAJECTORY)
            self.assertIsNone(panel.play_epoch)
            ticket=panel.play_pending[1]
            owner.state.command_results[ticket]['control_epoch']=9
            panel.after_cancel(panel._poll_job);panel._poll_job=None;panel.poll()
            self.assertEqual(panel.play_epoch,9);self.assertIsNone(panel.play_pending)
            self.assertEqual(len(owner.calls),1)
            panel.destroy()
        finally:root.destroy()


if __name__=='__main__':unittest.main()
