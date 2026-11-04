"""Offline actual-coordinate GUI compatibility; no serial or device commands."""
from collections import deque
from pathlib import Path
import sys
import tkinter as tk
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
sys.path.insert(0,str(ROOT/'work/host'))
from sequence_viewer import SequenceViewer
from test_measured_sequence import frame,manifest


class SequenceViewerTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
        self.viewer=SequenceViewer(self.root)
        self.root.update()

    def tearDown(self):
        self.viewer.close();self.root.destroy()

    def record(self,times=range(0,45,5)):
        return dict(format='J280SequentialInputV1',manifest=manifest(),
                    frames=[frame(t,encoder=11) for t in times])

    def test_constant_trace_renders_one_stationary_coordinate_without_templates(self):
        self.viewer.set_record(self.record());self.viewer.show_all();self.root.update()
        self.assertEqual(self.viewer.trace.status,'complete')
        self.assertEqual({(p['x_count'],p['y_rad_s']) for p in self.viewer.trace.points},{(0,0.)})
        items=self.viewer.canvas.find_withtag('measured_trace')
        self.assertTrue(items)
        centers=[]
        for item in items:
            coords=self.viewer.canvas.coords(item)
            centers.append(((coords[0]+coords[2])/2,(coords[1]+coords[3])/2))
        for x,y in centers:
            self.assertAlmostEqual(x,centers[0][0]);self.assertAlmostEqual(y,centers[0][1])
        self.assertEqual(self.viewer.canvas.find_withtag('display_connection'),())
        self.assertEqual(self.viewer.canvas.find_withtag('reference_guide'),())

    def test_gap_points_render_as_separate_marks_without_bridging(self):
        self.viewer.set_record(self.record([0,5,10,20,25,30]))
        self.viewer.show_all();self.root.update()
        self.assertEqual(len(self.viewer.trace.points),3)
        self.assertEqual(len(self.viewer.trace.breaks),2)
        items=self.viewer.canvas.find_withtag('measured_trace')
        self.assertEqual(len(items),3)
        self.assertTrue(all(self.viewer.canvas.type(item)=='oval' for item in items))

    def test_live_provider_stall_cannot_advance_to_future_letter(self):
        values=deque([dict(key='one',manifest=manifest(),frames=[frame(t) for t in (0,5,10)]),None,None])
        self.viewer.provider=lambda reset=False:values.popleft() if values else None
        def tick():
            if self.viewer.after_id is not None:self.viewer.after_cancel(self.viewer.after_id)
            self.viewer.tick();self.root.update()
        tick();points=self.viewer.trace.points;drawn=self.viewer.drawn
        self.assertEqual({p['phase'] for p in points},{'G'})
        for _ in range(2):
            tick();self.assertEqual(self.viewer.trace.points,points)
            self.assertEqual(self.viewer.drawn,drawn)
            self.assertEqual(self.viewer.trace.phase,'G')
        self.assertIn('反例测试数据',self.viewer.message.get())

    def test_top_controls_fit_in_the_normal_viewport(self):
        self.viewer.geometry('1120x740');self.root.update()
        bar=self.viewer.winfo_children()[0]
        children=bar.winfo_children()
        self.assertGreaterEqual(len(children),8)
        for child in children:
            self.assertGreater(child.winfo_width(),1)
            self.assertLessEqual(child.winfo_x()+child.winfo_width(),bar.winfo_width())


if __name__=='__main__':unittest.main()
