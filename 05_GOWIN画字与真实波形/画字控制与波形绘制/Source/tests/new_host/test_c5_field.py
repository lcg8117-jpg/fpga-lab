"""Reproduce the real missing-plot failure and exercise the new result panel."""
from pathlib import Path
import json,sys,unittest
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'host'))
from word_trajectory import measured_trace

class FieldTests(unittest.TestCase):
    def test_fixed_span_uses_only_actual_matched_encoder_history(self):
        from fixed_progress import FixedSpan
        span=FixedSpan();d=dict(motion_action=0x23,motion_request_id=9,board_ms=100)
        state=dict(latest_diagnostic=d,history=[(1,0,0,0,-1000,0,95),(2,0,0,0,-390,9999,100),(3,0,0,0,390,9999,105)])
        self.assertIn('270.00°',span.update(state));self.assertEqual((span.low,span.high),(-390,390))
        d['motion_request_id']=10;d['board_ms']=110;state['history']=[(4,0,0,0,5,9999,110)]
        self.assertIn('尚未观察到270°',span.update(state));self.assertEqual((span.low,span.high),(5,5))

    def test_lost_interior_sample_does_not_erase_two_real_endpoints(self):
        meta=dict(window_ms=20,word_start_ms=20,word_end_ms=45)
        rows=[(1+i,i*5,i*i,6,3,0,True)for i in range(10)if i!=1]
        result=measured_trace(rows,meta,0)
        points={p[0]:p for p in result if p is not None}
        self.assertIn(20,points)
        self.assertNotIn(25,points)  # t=5 endpoint really was absent.
        self.assertIn(30,points)
        self.assertIn(None,result)
        self.assertEqual(points[20][1],16)

    def test_saved_C4_run_recovers3599_unmodified_actual_pairs(self):
        import numpy as np
        with np.load(ROOT/'evidence/C4_GOWIN_saved_rows.npz')as source:
            rows=source['rows'].tolist();meta=json.loads(str(source['metadata_json']));epoch=int(source['ack_epoch'])
        values=measured_trace(rows,meta,epoch)
        points=[p for p in values if p is not None]
        self.assertEqual(len(points),3599)
        lookup={r[1]-rows[0][1]:r[2] for r in rows}
        for dt,x,y in points:
            self.assertEqual(x,lookup[dt])
            import math
            self.assertEqual(y,(x-lookup[dt-meta['window_ms']])*2*math.pi/1040/(meta['window_ms']/1000))

    def test_result_has_a_dedicated_page_and_remains_after_new_plan(self):
        import tkinter as tk
        from app import App
        root=tk.Tk();root.withdraw()
        try:
            app=App(root,5);panel=app.draw_editor
            self.assertIs(panel.xy.master,app.word_result_page)
            self.assertTrue(panel.separate_result)
            self.assertEqual(str(panel.xy.pack_info()['expand']),'1')
            panel.show_results();self.assertEqual(app.main_notebook.select(),str(app.word_result_page))
            actual=[(0,1,.1),(5,2,.2),None,(15,3,.3)]
            panel.last_actual=list(actual);panel.last_actual_metadata=dict(word='saved')
            panel.render(actual,'saved measured data','#1565c0')
            panel.generate_word('GOWIN')
            self.assertEqual(panel.last_actual,actual)
            self.assertEqual(panel._render_values,actual)
            self.assertFalse(root.winfo_ismapped())
            panel.destroy()
        finally:root.destroy()

if __name__=='__main__':unittest.main()
