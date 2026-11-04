"""Pure acquisition tests; no serial, UI, clock-driven progress or plant model."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'host'))
from measured_sequence import SequenceTrace, reconstruct, MAX_TASK_MS


def manifest():
    return dict(counts_per_rev=100, window_ms=10, play_ms=100, origin=11,
                request_id=2790, session_id='physical_1728', source_kind='synthetic_test',
                phases=[dict(name=name, start_ms=start, end_ms=end)
                        for name,start,end in [('prelude',0,10),('G',10,20),
                                               ('transition_G_O',20,25),('O',25,35),('tail',35,45)]])


def frame(t, encoder=None, **changes):
    result = dict(board_ms=100+t, control_epoch=1000+t,
                  encoder=11+t if encoder is None else encoder,
                  session_id='physical_1728', request_id=2790, fault=0, coherent=True)
    result.update(changes)
    return result


def feed(trace, times=range(0,45,5)):
    return [point for t in times if (point := trace.feed(frame(t))) is not None]


class MeasuredSequenceTests(unittest.TestCase):
    def test_constant_encoder_remains_one_stationary_coordinate(self):
        trace=SequenceTrace(manifest())
        for t in range(0,45,5): trace.feed(frame(t,encoder=11))
        self.assertEqual({(p['x_count'],p['y_rad_s']) for p in trace.points}, {(0,0.)})
        self.assertEqual(len(trace.points),7)

    def test_prelude_accumulates_exact_window_history(self):
        trace=SequenceTrace(manifest())
        self.assertIsNone(trace.feed(frame(0)))
        self.assertIsNone(trace.feed(frame(5)))
        point=trace.feed(frame(10))
        self.assertEqual(point['delayed_encoder'],11)
        self.assertEqual(point['x_count'],10)
        self.assertAlmostEqual(point['y_rad_s'],20*math.pi)
        self.assertEqual(trace.counts['accepted_frames'],3)

    def test_no_data_cannot_advance_phase_or_generate_points(self):
        trace=SequenceTrace(manifest());before=trace.export()
        for _ in range(20):
            self.assertEqual(trace.export(),before)
            self.assertEqual(trace.status,'waiting')
            self.assertIsNone(trace.phase)

    def test_incremental_point_suffix_is_bounded_and_does_not_alias_state(self):
        trace=SequenceTrace(manifest());feed(trace,range(0,25,5))
        self.assertEqual(trace.point_count,3)
        original=trace.points
        suffix=trace.points_since(1)
        self.assertEqual(suffix,original[1:])
        suffix[0]['encoder']=99999
        self.assertEqual(trace.points,original)
        self.assertEqual(trace.points_since(trace.point_count),[])
        for index in (-1,True,4):
            with self.subTest(index=index),self.assertRaises(ValueError):trace.points_since(index)

    def test_duplicates_and_reversed_frames_do_not_poison_baseline(self):
        trace=SequenceTrace(manifest());feed(trace,range(0,15,5))
        baseline=deepcopy(trace.points)
        self.assertIsNone(trace.feed(frame(10,encoder=99999)))
        self.assertIsNone(trace.feed(frame(5,encoder=-99999)))
        self.assertEqual(trace.points,baseline)
        point=trace.feed(frame(15))
        self.assertEqual(point['delayed_encoder'],16)
        self.assertAlmostEqual(point['y_rad_s'],20*math.pi)
        self.assertEqual(trace.counts['duplicates'],1)
        self.assertEqual(trace.counts['reversed'],1)
        self.assertEqual(trace.breaks,[])

    def test_gap_keeps_exact_past_endpoint_but_breaks_line(self):
        trace=SequenceTrace(manifest());feed(trace,range(0,15,5))
        point=trace.feed(frame(20))
        self.assertEqual(point['delayed_encoder'],21)
        self.assertEqual(trace.breaks[0]['before_point_index'],1)
        self.assertIn('missing_frames',trace.breaks[0]['reasons'])
        self.assertIsNone(trace.feed(frame(25)))
        resumed=trace.feed(frame(30))
        self.assertEqual(resumed['delayed_encoder'],31)
        self.assertIn('missing_endpoint',trace.breaks[-1]['reasons'])

    def test_nearby_endpoint_never_substitutes_for_missing_exact_endpoint(self):
        trace=SequenceTrace(manifest())
        trace.feed(frame(0));trace.feed(frame(10))
        self.assertIsNone(trace.feed(frame(15)))
        self.assertEqual([p['relative_ms'] for p in trace.points],[10])

    def test_epoch_mismatch_rejects_without_updating_time_anchor(self):
        trace=SequenceTrace(manifest());feed(trace,range(0,15,5))
        self.assertIsNone(trace.feed(frame(15,control_epoch=1014)))
        self.assertEqual(trace.counts['accepted_frames'],3)
        self.assertIsNotNone(trace.feed(frame(20)))
        self.assertEqual(trace.counts['epoch_mismatch'],1)
        self.assertEqual(trace.counts['missing_frames'],1)

    def test_fault_locks_task_without_advancing_measured_trace(self):
        trace=SequenceTrace(manifest());feed(trace,range(0,15,5))
        before=trace.points
        self.assertIsNone(trace.feed(frame(15,fault=7)))
        self.assertEqual(trace.status,'fault')
        self.assertIsNone(trace.feed(frame(20)))
        self.assertEqual(trace.points,before)
        self.assertEqual(trace.phase,'G')

    def test_cancel_locks_without_time_or_phase_advance(self):
        trace=SequenceTrace(manifest());feed(trace,range(0,15,5))
        before=trace.points;trace.cancel()
        self.assertIsNone(trace.feed(frame(15)))
        self.assertEqual(trace.points,before)
        self.assertEqual((trace.status,trace.phase),('cancelled','G'))

    def test_session_or_request_change_requires_new_object(self):
        for changes in ({'session_id':'reconnected'},{'request_id':2791}):
            with self.subTest(changes=changes):
                trace=SequenceTrace(manifest());feed(trace,range(0,15,5))
                trace.feed(frame(15,**changes))
                self.assertEqual(trace.status,'invalidated')
                self.assertIsNone(trace.feed(frame(20)))
                other=manifest();other.update(changes)
                replacement=SequenceTrace(other)
                for t in (0,5,10):point=replacement.feed(frame(t,**changes))
                self.assertIsNotNone(point)

    def test_origin_is_signed_fixed_and_manifest_is_not_aliased(self):
        for origin in (0,-20,11):
            with self.subTest(origin=origin):
                source=manifest();source['origin']=origin
                trace=SequenceTrace(source);source['origin']=999
                for t in (0,5,10):point=trace.feed(frame(t,origin=888))
                self.assertEqual(point['x_count'],21-origin)
                self.assertEqual(trace.manifest['origin'],origin)

    def test_phase_boundaries_assign_each_current_sample_exactly_once(self):
        trace=SequenceTrace(manifest());points=feed(trace)
        self.assertEqual([(p['relative_ms'],p['phase']) for p in points],
                         [(10,'G'),(15,'G'),(20,'transition_G_O'),(25,'O'),
                          (30,'O'),(35,'tail'),(40,'tail')])
        self.assertEqual(len({p['board_ms'] for p in points}),len(points))

    def test_prefix_is_independent_of_future_encoders(self):
        a=SequenceTrace(manifest());b=SequenceTrace(manifest())
        feed(a,range(0,25,5));prefix=a.points
        feed(b)
        self.assertEqual(prefix,[p for p in b.points if p['relative_ms']<=20])
        for t in range(25,45,5):a.feed(frame(t,encoder=-100000+t))
        self.assertEqual(prefix,[p for p in a.points if p['relative_ms']<=20])

    def test_history_is_bounded_and_task_completion_is_terminal(self):
        trace=SequenceTrace(manifest())
        for t in range(0,45,5):
            trace.feed(frame(t));self.assertLessEqual(trace.history_size,3)
        before=trace.points
        self.assertEqual(trace.status,'complete')
        self.assertIsNone(trace.feed(frame(45)))
        self.assertEqual(trace.points,before)

    def test_incoherent_frame_is_not_used_as_endpoint(self):
        trace=SequenceTrace(manifest());trace.feed(frame(0))
        trace.feed(frame(5,coherent=False));trace.feed(frame(10))
        self.assertIsNone(trace.feed(frame(15)))
        self.assertEqual(trace.counts['incoherent'],1)

    def test_strict_manifest_rejects_malformed_or_unbounded_task(self):
        variants=[]
        for key,value in [('origin',True),('request_id',0),('session_id','bad space'),
                          ('source_kind','unknown'),('window_ms',7)]:
            data=manifest();data[key]=value;variants.append(data)
        data=manifest();data['phases'][1]['start_ms']=15;variants.append(data)
        data=manifest();data['phases'][-1]['end_ms']=MAX_TASK_MS+5;variants.append(data)
        data=manifest();data['phases'][1]['name']='prelude';variants.append(data)
        for data in variants:
            with self.subTest(data=data),self.assertRaises(ValueError):SequenceTrace(data)

    def test_bad_frame_types_and_alignment_are_rejected_without_progress(self):
        trace=SequenceTrace(manifest())
        for bad in [None,{},frame(0,control_epoch=0),frame(0,encoder=True),
                    frame(0,coherent=1),frame(1),frame(0,session_id='bad space')]:
            self.assertIsNone(trace.feed(bad))
        self.assertEqual(trace.counts['accepted_frames'],0)
        self.assertEqual(trace.status,'waiting')
        self.assertIsNotNone(feed(trace)[0])

    def test_exports_rebuild_points_breaks_and_terminal_state_from_raw_frames(self):
        for terminal in ('complete','fault','cancel','identity'):
            with self.subTest(terminal=terminal):
                trace=SequenceTrace(manifest());feed(trace,[0,5,10,20,25,30])
                if terminal=='complete':feed(trace,[35,40])
                elif terminal=='fault':trace.feed(frame(35,fault=7))
                elif terminal=='cancel':trace.cancel('test_cancel')
                else:trace.feed(frame(35,session_id='other_session'))
                exported=json.loads(json.dumps(trace.export(),allow_nan=False))
                rebuilt=reconstruct(exported)
                self.assertEqual(rebuilt.points,trace.points)
                self.assertEqual(rebuilt.breaks,trace.breaks)
                self.assertEqual(rebuilt.status,trace.status)
                self.assertEqual(exported['declared_source_kind'],'synthetic_test')
                self.assertFalse(exported['physical_control_verified'])
                exported['points'][0]['x_count']+=1
                with self.assertRaises(ValueError):reconstruct(exported)


if __name__=='__main__':unittest.main()
